"""AWS S3 / MinIO Medallion Data Lake Adapter with Zstandard Parquet and Forensics.

Implements DataLakePort using dual S3 boto3 synchronization and a local columnar
mirror so the pipeline executes deterministically in offline CI/Pytest while
populating live MinIO buckets (:9000 API / :9001 Console) when active.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import boto3
import polars as pl
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from src.domain.contracts import _sanitize_for_json
from src.domain.entities import QuarantineRecordEntity
from src.domain.invariants import CANONICAL_POLARS_SCHEMA, BatchInvariantReport
from src.infrastructure.config import AssetPipeConfig

QUARANTINE_POLARS_SCHEMA: dict[str, pl.DataType] = {
    "batch_id": pl.String, "position_id": pl.String, "portfolio_id": pl.String,
    "isin": pl.String, "valuation_date": pl.String, "rule_id": pl.String,
    "violation_code": pl.String, "diagnostic_message": pl.String,
    "raw_payload_json": pl.String, "quarantined_at_utc": pl.String,
}


def _is_s3_endpoint_reachable(endpoint_url: str) -> bool:
    """Perform a fast TCP socket probe to check if S3/MinIO is listening."""
    parsed = urlparse(endpoint_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 9000)
    try:
        with socket.create_connection((host, port), timeout=0.15):
            return True
    except OSError:
        return False


class S3ParquetAdapter:
    """Medallion 3-bucket Data Lake adapter writing ZSTD Parquet and forensic manifests."""

    def __init__(self, config: AssetPipeConfig | None = None) -> None:
        self.config = config or AssetPipeConfig.from_env()
        self._s3_online = _is_s3_endpoint_reachable(self.config.s3_endpoint_url)
        self._s3 = (
            boto3.client(
                "s3",
                endpoint_url=self.config.s3_endpoint_url,
                aws_access_key_id=self.config.s3_access_key,
                aws_secret_access_key=self.config.s3_secret_key,
                region_name="us-east-1",
                config=BotoConfig(
                    connect_timeout=1, read_timeout=2, retries={"max_attempts": 1}
                ),
            )
            if self._s3_online
            else None
        )
        self.ensure_buckets_exist()

    def ensure_buckets_exist(self) -> None:
        """Create local mirror directories and S3/MinIO buckets idempotently."""
        for bucket in self.config.all_buckets:
            (self.config.local_lake_root / bucket).mkdir(parents=True, exist_ok=True)
            if self._s3 is not None:
                try:
                    self._s3.head_bucket(Bucket=bucket)
                except (BotoCoreError, ClientError):
                    try:
                        self._s3.create_bucket(Bucket=bucket)
                    except (BotoCoreError, ClientError):
                        self._s3_online = False

    def _put_bytes(
        self,
        bucket: str,
        key: str,
        payload: bytes,
        content_type: str = "application/octet-stream",
    ) -> Path:
        """Persist object bytes to the local mirror and sync to S3/MinIO if online."""
        local_path = self.config.local_lake_root / bucket / key
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(payload)
        if self._s3_online and self._s3 is not None:
            try:
                self._s3.put_object(
                    Bucket=bucket, Key=key, Body=payload, ContentType=content_type
                )
            except (BotoCoreError, ClientError):
                self._s3_online = False
        return local_path

    def write_landing_batch(
        self,
        batch_id: str,
        raw_records: list[dict[str, object]],
        valuation_date: str,
    ) -> str:
        """Persist raw batch to landing/valuation_date={valuation_date}/{batch_id}_raw.parquet."""
        key = f"valuation_date={valuation_date}/{batch_id}_raw.parquet"
        rows = [
            {
                "batch_id": batch_id,
                "row_index": idx,
                "raw_payload_json": json.dumps(
                    _sanitize_for_json(rec), default=str, sort_keys=True, allow_nan=False
                ),
            }
            for idx, rec in enumerate(raw_records)
        ]
        schema = {"batch_id": pl.String, "row_index": pl.Int64, "raw_payload_json": pl.String}
        df = pl.DataFrame(rows, schema=schema)
        buf = io.BytesIO()
        df.write_parquet(buf, compression="zstd", statistics=True)
        self._put_bytes(self.config.bucket_landing, key, buf.getvalue())
        return f"s3://{self.config.bucket_landing}/{key}"

    def write_validated_parquet(
        self,
        batch_id: str,
        clean_df: pl.DataFrame,
        valuation_date: str,
    ) -> dict[str, object]:
        """Write validated positions with ZSTD compression, statistics, and SHA-256."""
        key = f"valuation_date={valuation_date}/{batch_id}_valid.parquet"
        buf = io.BytesIO()
        clean_df.write_parquet(buf, compression="zstd", statistics=True)
        payload = buf.getvalue()
        sha256_hex = hashlib.sha256(payload).hexdigest()
        local_path = self._put_bytes(self.config.bucket_validated, key, payload)
        return {
            "batch_id": batch_id, "bucket": self.config.bucket_validated, "key": key,
            "s3_uri": f"s3://{self.config.bucket_validated}/{key}",
            "local_path": str(local_path), "row_count": clean_df.height,
            "size_bytes": len(payload), "compression": "ZSTD", "sha256": sha256_hex,
        }

    def write_quarantine_artifacts(
        self,
        batch_id: str,
        quarantined: list[QuarantineRecordEntity],
        report: BatchInvariantReport,
        valuation_date: str,
        source_uri: str,
    ) -> dict[str, object]:
        """Write ZSTD quarantine Parquet and forensic .manifest.json with SHA-256."""
        pq_key = f"valuation_date={valuation_date}/{batch_id}_quarantine.parquet"
        mf_key = f"valuation_date={valuation_date}/{batch_id}_quarantine.manifest.json"
        q_rows = [q.to_dict() for q in quarantined]
        q_df = pl.DataFrame(q_rows, schema=QUARANTINE_POLARS_SCHEMA)
        buf = io.BytesIO()
        q_df.write_parquet(buf, compression="zstd", statistics=True)
        pq_bytes = buf.getvalue()
        sha256_hex = hashlib.sha256(pq_bytes).hexdigest()
        pq_path = self._put_bytes(self.config.bucket_quarantine, pq_key, pq_bytes)

        rule_counts: dict[str, int] = {}
        for item in quarantined:
            rule_counts[item.rule_id] = rule_counts.get(item.rule_id, 0) + 1

        manifest: dict[str, object] = {
            "batch_id": batch_id,
            "valuation_date": valuation_date,
            "created_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_uri": source_uri,
            "quarantine_parquet_uri": f"s3://{self.config.bucket_quarantine}/{pq_key}",
            "sha256": sha256_hex,
            "compression": "ZSTD",
            "quarantined_records_count": len(quarantined),
            "rule_violation_summary": rule_counts,
            "invariant_report": report.to_dict(),
        }
        mf_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        mf_path = self._put_bytes(
            self.config.bucket_quarantine, mf_key, mf_bytes, content_type="application/json"
        )
        return {
            "batch_id": batch_id,
            "parquet_uri": f"s3://{self.config.bucket_quarantine}/{pq_key}",
            "manifest_uri": f"s3://{self.config.bucket_quarantine}/{mf_key}",
            "parquet_local_path": str(pq_path), "manifest_local_path": str(mf_path),
            "sha256": sha256_hex, "quarantined_count": len(quarantined), "manifest": manifest,
        }

    def scan_validated_parquet(self, valuation_date: str | None = None) -> pl.LazyFrame:
        """Scan validated ZSTD Parquet files lazily or return an empty typed LazyFrame."""
        base_dir = self.config.local_lake_root / self.config.bucket_validated
        target_dir = base_dir / f"valuation_date={valuation_date}" if valuation_date else base_dir
        files = sorted(target_dir.rglob("*.parquet")) if target_dir.exists() else []
        if not files:
            return pl.DataFrame([], schema=CANONICAL_POLARS_SCHEMA).lazy()
        return pl.scan_parquet([str(f) for f in files])

    def get_lake_storage_metrics(self) -> dict[str, object]:
        """Compute object counts and byte usage across landing, validated, and quarantine."""
        buckets_info: dict[str, dict[str, int]] = {}
        total_objects = 0
        total_bytes = 0
        for bucket in self.config.all_buckets:
            b_dir = self.config.local_lake_root / bucket
            files = [f for f in b_dir.rglob("*") if f.is_file()] if b_dir.exists() else []
            b_bytes = sum(f.stat().st_size for f in files)
            buckets_info[bucket] = {"object_count": len(files), "size_bytes": b_bytes}
            total_objects += len(files)
            total_bytes += b_bytes
        return {
            "s3_endpoint_url": self.config.s3_endpoint_url,
            "s3_console_url": self.config.s3_public_console_url,
            "s3_online": self._s3_online,
            "total_objects": total_objects,
            "total_bytes": total_bytes,
            "buckets": buckets_info,
        }

    def reset_lake_to_zero(self) -> None:
        """Purge all objects across the three Medallion buckets back to zero."""
        for bucket in self.config.all_buckets:
            b_dir = self.config.local_lake_root / bucket
            if b_dir.exists():
                shutil.rmtree(b_dir)
            b_dir.mkdir(parents=True, exist_ok=True)
            if self._s3_online and self._s3 is not None:
                try:
                    listed = self._s3.list_objects_v2(Bucket=bucket).get("Contents", [])
                    for obj in listed:
                        self._s3.delete_object(Bucket=bucket, Key=obj["Key"])
                except (BotoCoreError, ClientError):
                    self._s3_online = False
