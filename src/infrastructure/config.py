"""Immutable Typed Environment Configuration for Asset-Pipe Infrastructure.

Resolves S3/MinIO endpoints (:9000 API, :9001 Console), 3-bucket Medallion
topology, local columnar mirror paths, and Dual Warehouse routing (DuckDB /
Snowflake RSA-2048) using relative repository resolution (OPSEC Law 2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class AssetPipeConfig:
    """Immutable infrastructure configuration resolved from environment variables."""

    app_env: str = "development"
    web_port: int = 3000
    sql_ui_port: int = 4213
    s3_endpoint_url: str = "http://localhost:9000"
    s3_public_console_url: str = "http://localhost:9001"
    s3_access_key: str = "admin"
    s3_secret_key: str = "assetpipe2026"
    bucket_landing: str = "landing"
    bucket_validated: str = "validated-parquet"
    bucket_quarantine: str = "quarantine"
    local_lake_root: Path = REPO_ROOT / "data" / "s3_lake"
    warehouse_engine: str = "duckdb"
    duckdb_path: Path = REPO_ROOT / "data" / "asset_pipe.duckdb"
    snowflake_account: str = "org-account_identifier"
    snowflake_user: str = "ASSET_PIPE_SVC_USER"
    snowflake_role: str = "ASSET_PIPE_SVC_ROLE"
    snowflake_warehouse: str = "ASSET_PIPE_WH"
    snowflake_database: str = "ASSET_PIPE_DB"
    snowflake_schema: str = "RECONCILIATION"
    snowflake_private_key_path: str = "/secrets/rsa_key.p8"

    @property
    def s3_bucket_landing(self) -> str:
        """Alias for landing bucket name."""
        return self.bucket_landing

    @property
    def s3_bucket_validated(self) -> str:
        """Alias for validated-parquet bucket name."""
        return self.bucket_validated

    @property
    def s3_bucket_quarantine(self) -> str:
        """Alias for quarantine bucket name."""
        return self.bucket_quarantine

    @property
    def all_buckets(self) -> tuple[str, str, str]:
        """Return the ordered tuple of all three Medallion lake buckets."""
        return (self.bucket_landing, self.bucket_validated, self.bucket_quarantine)

    @classmethod
    def from_env(
        cls,
        local_lake_override: Path | None = None,
        duckdb_override: Path | None = None,
    ) -> AssetPipeConfig:
        """Construct an immutable AssetPipeConfig from environment and optional overrides."""
        raw_lake = os.getenv("ASSET_PIPE_LAKE_DIR")
        lake_root = (
            local_lake_override.resolve()
            if local_lake_override is not None
            else (Path(raw_lake).resolve() if raw_lake else REPO_ROOT / "data" / "s3_lake")
        )
        raw_duck = os.getenv("DUCKDB_PATH", "data/asset_pipe.duckdb")
        duck_candidate = Path(raw_duck)
        if not duck_candidate.is_absolute():
            duck_candidate = REPO_ROOT / duck_candidate
        duck_path = (
            duckdb_override.resolve()
            if duckdb_override is not None
            else duck_candidate.resolve()
        )
        engine = os.getenv("WAREHOUSE_ENGINE", "duckdb").strip().lower()
        if engine not in {"duckdb", "snowflake"}:
            engine = "duckdb"

        return cls(
            app_env=os.getenv("APP_ENV", "development"),
            web_port=int(os.getenv("PORT", "3000")),
            sql_ui_port=int(os.getenv("SQL_UI_PORT", "4213")),
            s3_endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://localhost:9000"),
            s3_public_console_url=os.getenv(
                "S3_PUBLIC_CONSOLE_URL", "http://localhost:9001"
            ),
            s3_access_key=os.getenv("S3_ACCESS_KEY", "admin"),
            s3_secret_key=os.getenv("S3_SECRET_KEY", "assetpipe2026"),
            bucket_landing=os.getenv("S3_BUCKET_LANDING", "landing"),
            bucket_validated=os.getenv("S3_BUCKET_VALIDATED", "validated-parquet"),
            bucket_quarantine=os.getenv("S3_BUCKET_QUARANTINE", "quarantine"),
            local_lake_root=lake_root,
            warehouse_engine=engine,
            duckdb_path=duck_path,
            snowflake_account=os.getenv("SNOWFLAKE_ACCOUNT", "org-account_identifier"),
            snowflake_user=os.getenv("SNOWFLAKE_USER", "ASSET_PIPE_SVC_USER"),
            snowflake_role=os.getenv("SNOWFLAKE_ROLE", "ASSET_PIPE_SVC_ROLE"),
            snowflake_warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "ASSET_PIPE_WH"),
            snowflake_database=os.getenv("SNOWFLAKE_DATABASE", "ASSET_PIPE_DB"),
            snowflake_schema=os.getenv("SNOWFLAKE_SCHEMA", "RECONCILIATION"),
            snowflake_private_key_path=os.getenv(
                "SNOWFLAKE_PRIVATE_KEY_PATH", "/secrets/rsa_key.p8"
            ),
        )
