"""Pytest Suite for Hexagonal Ports, AssetPipeConfig, and S3ParquetAdapter.

Verifies DataLakePort runtime protocol compliance, 3-bucket creation, physical
PyArrow ZSTD compression and zone-map statistics, cryptographic SHA-256 sidecar
manifests (.manifest.json), lazy Polars scanning, and reset_lake_to_zero().
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

from src.application.ports import DataLakePort
from src.domain.invariants import CANONICAL_POLARS_SCHEMA, audit_and_bifurcate_batch
from src.infrastructure.config import AssetPipeConfig
from src.infrastructure.s3_parquet_adapter import S3ParquetAdapter


def _sample_mixed_batch() -> list[dict[str, object]]:
    """Return a 4-record batch (3 valid positions and 1 corrupted ISIN record)."""
    return [
        {
            "position_id": "POS-AAPL-01",
            "portfolio_id": "PF-GLOBAL-01",
            "isin": "US0378331005",
            "valuation_date": "2026-11-10",
            "currency": "USD",
            "units": 1000.0,
            "price": 225.0,
            "fx_rate": 1.0,
            "custodian_reported_nav": 225000.0,
            "desk_code": "NY-EQ",
        },
        {
            "position_id": "POS-MSFT-02",
            "portfolio_id": "PF-GLOBAL-01",
            "isin": "US5949181045",
            "valuation_date": "2026-11-10",
            "currency": "USD",
            "units": 500.0,
            "price": 412.0,
            "fx_rate": 1.0,
            "custodian_reported_nav": 206000.0,
            "desk_code": "NY-EQ",
        },
        {
            "position_id": "POS-SAN-03",
            "portfolio_id": "PF-GLOBAL-01",
            "isin": "ES0113900J37",
            "valuation_date": "2026-11-10",
            "currency": "EUR",
            "units": 10000.0,
            "price": 4.60,
            "fx_rate": 1.08,
            "custodian_reported_nav": 49680.0,
            "desk_code": "MAD-RATES",
        },
        {
            "position_id": "POS-BAD-ISIN-04",
            "portfolio_id": "PF-GLOBAL-01",
            "isin": "US0378331009",
            "valuation_date": "2026-11-10",
            "currency": "USD",
            "units": 100.0,
            "price": 225.0,
            "fx_rate": 1.0,
            "custodian_reported_nav": 22500.0,
        },
    ]


def test_config_and_datalake_port_protocol_compliance(tmp_path: Path) -> None:
    """Verify AssetPipeConfig overrides and S3ParquetAdapter satisfies DataLakePort."""
    cfg = AssetPipeConfig.from_env(
        local_lake_override=tmp_path / "lake",
        duckdb_override=tmp_path / "test.duckdb",
    )
    assert cfg.s3_endpoint_url.startswith("http")
    assert cfg.s3_public_console_url.endswith(":9001")
    assert cfg.all_buckets == ("landing", "validated-parquet", "quarantine")
    assert cfg.s3_bucket_landing == "landing"
    assert cfg.s3_bucket_validated == "validated-parquet"
    assert cfg.s3_bucket_quarantine == "quarantine"

    adapter = S3ParquetAdapter(cfg)
    assert isinstance(adapter, DataLakePort)
    for bucket in cfg.all_buckets:
        assert (cfg.local_lake_root / bucket).is_dir()


def test_write_validated_parquet_enforces_zstd_codec_and_statistics(tmp_path: Path) -> None:
    """Verify physical Parquet files use ZSTD compression, statistics, and SHA-256."""
    cfg = AssetPipeConfig.from_env(local_lake_override=tmp_path / "lake")
    adapter = S3ParquetAdapter(cfg)
    raw_batch = _sample_mixed_batch()

    landing_uri = adapter.write_landing_batch("BATCH-ZSTD-01", raw_batch, "2026-11-10")
    assert landing_uri == "s3://landing/valuation_date=2026-11-10/BATCH-ZSTD-01_raw.parquet"

    clean_df, quarantined, _ = audit_and_bifurcate_batch(raw_batch, "BATCH-ZSTD-01")
    assert clean_df.height == 3
    assert len(quarantined) == 1

    val_meta = adapter.write_validated_parquet("BATCH-ZSTD-01", clean_df, "2026-11-10")
    assert val_meta["s3_uri"] == (
        "s3://validated-parquet/valuation_date=2026-11-10/BATCH-ZSTD-01_valid.parquet"
    )
    assert val_meta["row_count"] == 3
    assert val_meta["compression"] == "ZSTD"

    local_pq = Path(str(val_meta["local_path"]))
    assert local_pq.is_file()
    expected_sha = hashlib.sha256(local_pq.read_bytes()).hexdigest()
    assert val_meta["sha256"] == expected_sha

    pq_metadata = pq.read_metadata(local_pq)
    assert pq_metadata.num_rows == 3
    assert pq_metadata.num_row_groups >= 1
    for col_idx in range(pq_metadata.num_columns):
        col_chunk = pq_metadata.row_group(0).column(col_idx)
        assert col_chunk.compression == "ZSTD"
        assert col_chunk.is_stats_set is True


def test_write_quarantine_artifacts_creates_manifest_json_and_sha256(tmp_path: Path) -> None:
    """Verify quarantine Parquet and sidecar .manifest.json match SHA-256 and GX rules."""
    cfg = AssetPipeConfig.from_env(local_lake_override=tmp_path / "lake")
    adapter = S3ParquetAdapter(cfg)
    raw_batch = _sample_mixed_batch()

    landing_uri = adapter.write_landing_batch("BATCH-Q-02", raw_batch, "2026-11-10")
    _, quarantined, report = audit_and_bifurcate_batch(raw_batch, "BATCH-Q-02")

    q_meta = adapter.write_quarantine_artifacts(
        batch_id="BATCH-Q-02",
        quarantined=quarantined,
        report=report,
        valuation_date="2026-11-10",
        source_uri=landing_uri,
    )
    pq_path = Path(str(q_meta["parquet_local_path"]))
    mf_path = Path(str(q_meta["manifest_local_path"]))
    assert pq_path.is_file()
    assert mf_path.is_file()

    pq_meta = pq.read_metadata(pq_path)
    assert pq_meta.num_rows == 1
    assert pq_meta.row_group(0).column(0).compression == "ZSTD"

    manifest = json.loads(mf_path.read_text(encoding="utf-8"))
    actual_sha256 = hashlib.sha256(pq_path.read_bytes()).hexdigest()
    assert manifest["sha256"] == actual_sha256
    assert manifest["source_uri"] == landing_uri
    assert manifest["quarantined_records_count"] == 1
    assert manifest["rule_violation_summary"] == {"GX-03: ISIN_ISO6166_REGEX": 1}
    assert manifest["invariant_report"]["batch_id"] == "BATCH-Q-02"


def test_scan_validated_parquet_metrics_and_reset_lake_to_zero(tmp_path: Path) -> None:
    """Verify lazy scanning on empty/populated lake, storage metrics, and zero reset."""
    cfg = AssetPipeConfig.from_env(local_lake_override=tmp_path / "lake")
    adapter = S3ParquetAdapter(cfg)

    empty_scan = adapter.scan_validated_parquet().collect()
    assert empty_scan.height == 0
    assert dict(empty_scan.schema) == CANONICAL_POLARS_SCHEMA

    raw_batch = _sample_mixed_batch()
    landing_uri = adapter.write_landing_batch("BATCH-SCAN-03", raw_batch, "2026-11-10")
    clean_df, quarantined, report = audit_and_bifurcate_batch(raw_batch, "BATCH-SCAN-03")
    adapter.write_validated_parquet("BATCH-SCAN-03", clean_df, "2026-11-10")
    adapter.write_quarantine_artifacts(
        "BATCH-SCAN-03", quarantined, report, "2026-11-10", landing_uri
    )

    scanned_df = adapter.scan_validated_parquet("2026-11-10").collect()
    assert scanned_df.height == 3
    assert sorted(scanned_df["position_id"].to_list()) == [
        "POS-AAPL-01",
        "POS-MSFT-02",
        "POS-SAN-03",
    ]

    metrics = adapter.get_lake_storage_metrics()
    assert int(metrics["total_objects"]) == 4  # type: ignore[arg-type]
    assert int(metrics["total_bytes"]) > 0  # type: ignore[arg-type]

    adapter.reset_lake_to_zero()
    zero_metrics = adapter.get_lake_storage_metrics()
    assert zero_metrics["total_objects"] == 0
    assert zero_metrics["total_bytes"] == 0
    assert adapter.scan_validated_parquet().collect().height == 0
