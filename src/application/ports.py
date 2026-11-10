"""Hexagonal Architecture Ports for Medallion S3 Data Lake and Dual Warehouse.

Defines runtime-checkable structural protocols decoupling the pure domain and
application orchestrators from concrete infrastructure adapters (AWS S3 / MinIO,
DuckDB Local Analytical Engine, and Snowflake Enterprise Cloud).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import polars as pl

from src.domain.entities import ExecutiveKpiSummary, QuarantineRecordEntity
from src.domain.invariants import BatchInvariantReport


@runtime_checkable
class DataLakePort(Protocol):
    """Port contract for the 3-bucket S3/MinIO Apache Parquet ZSTD Medallion Lake."""

    def ensure_buckets_exist(self) -> None:
        """Guarantee that landing, validated-parquet, and quarantine buckets exist."""
        ...

    def write_landing_batch(
        self,
        batch_id: str,
        raw_records: list[dict[str, object]],
        valuation_date: str,
    ) -> str:
        """Persist raw unvalidated records to landing/ and return the S3 URI."""
        ...

    def write_validated_parquet(
        self,
        batch_id: str,
        clean_df: pl.DataFrame,
        valuation_date: str,
    ) -> dict[str, object]:
        """Write GX-validated positions as ZSTD Parquet with SHA-256 and statistics."""
        ...

    def write_quarantine_artifacts(
        self,
        batch_id: str,
        quarantined: list[QuarantineRecordEntity],
        report: BatchInvariantReport,
        valuation_date: str,
        source_uri: str,
    ) -> dict[str, object]:
        """Persist rejected records as ZSTD Parquet alongside a .manifest.json file."""
        ...

    def scan_validated_parquet(
        self,
        valuation_date: str | None = None,
    ) -> pl.LazyFrame:
        """Return a lazy Polars scan over validated Parquet partitions."""
        ...

    def get_lake_storage_metrics(self) -> dict[str, object]:
        """Compute object counts and byte footprints across the three lake buckets."""
        ...

    def reset_lake_to_zero(self) -> None:
        """Purge all objects across landing, validated-parquet, and quarantine buckets."""
        ...


@runtime_checkable
class WarehousePort(Protocol):
    """Port contract for Dual Analytical Warehouses (DuckDB and Snowflake)."""

    def initialize_schema(self) -> None:
        """Initialize analytical tables and views idempotently."""
        ...

    def upsert_reconciled_positions(
        self,
        reconciled_df: pl.DataFrame,
        batch_id: str,
    ) -> int:
        """Idempotently upsert reconciled positions and return affected row count."""
        ...

    def insert_quarantine_records(
        self,
        quarantined: list[QuarantineRecordEntity],
    ) -> int:
        """Persist forensic quarantine records into the analytical audit table."""
        ...

    def get_executive_kpis(self) -> ExecutiveKpiSummary:
        """Return executive KPI aggregates or Honest Zero Baseline ($0.00) when empty."""
        ...

    def query_positions_paginated(
        self,
        status_filter: str = "ALL",
        search_query: str = "",
        page: int = 1,
        page_size: int = 25,
        sort_by: str = "nav_break_abs_desc",
    ) -> dict[str, object]:
        """Query reconciled positions with server-side filtering, sorting, and paging."""
        ...

    def execute_readonly_sql(self, sql_query: str) -> dict[str, object]:
        """Execute a read-only analytical SQL query (`SELECT` / `WITH` / `DESCRIBE`)."""
        ...

    def reset_warehouse_to_zero(self) -> None:
        """Truncate all analytical tables back to the Honest Zero Baseline ($0.00)."""
        ...
