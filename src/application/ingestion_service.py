"""Application Orchestrator for Batch Ingestion, Customs Gate, and Polars Reconciliation.

Coordinates the hexagonal DataLakePort (S3/MinIO ZSTD Parquet & Quarantine),
dual-gate customs validation (Pydantic v2 + Polars GX-01..GX-07), the vectorized
PolarsReconciliationEngine, and the analytical WarehousePort (DuckDB / Snowflake).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import polars as pl

from src.application.ports import DataLakePort, WarehousePort
from src.application.reconciliation_engine import PolarsReconciliationEngine
from src.domain.entities import ExecutiveKpiSummary, ReconciliationStatus
from src.domain.invariants import audit_and_bifurcate_batch


def _summarize_reconciled_df(reconciled_df: pl.DataFrame) -> dict[str, int | float]:
    """Compute vectorized status counts and AUM totals from a reconciled DataFrame."""
    if reconciled_df.height == 0:
        return {
            "reconciled_count": 0,
            "minor_tolerance_count": 0,
            "nav_break_count": 0,
            "ibor_aum_usd": 0.0,
            "custodian_aum_usd": 0.0,
            "gross_nav_variance_usd": 0.0,
            "net_nav_variance_usd": 0.0,
        }
    stats = (
        reconciled_df.lazy()
        .select(
            reconciled_count=(
                pl.col("reconciliation_status") == ReconciliationStatus.RECONCILED.value
            ).sum(),
            minor_tolerance_count=(
                pl.col("reconciliation_status") == ReconciliationStatus.MINOR_TOLERANCE.value
            ).sum(),
            nav_break_count=(
                pl.col("reconciliation_status") == ReconciliationStatus.NAV_BREAK.value
            ).sum(),
            ibor_aum_usd=pl.col("calculated_nav").sum().round(4),
            custodian_aum_usd=pl.col("custodian_reported_nav").sum().round(4),
            gross_nav_variance_usd=pl.col("nav_break_abs").sum().round(4),
            net_nav_variance_usd=pl.col("nav_break_signed").sum().round(4),
        )
        .collect()
        .row(0, named=True)
    )
    return {
        "reconciled_count": int(stats["reconciled_count"]),
        "minor_tolerance_count": int(stats["minor_tolerance_count"]),
        "nav_break_count": int(stats["nav_break_count"]),
        "ibor_aum_usd": float(stats["ibor_aum_usd"]),
        "custodian_aum_usd": float(stats["custodian_aum_usd"]),
        "gross_nav_variance_usd": float(stats["gross_nav_variance_usd"]),
        "net_nav_variance_usd": float(stats["net_nav_variance_usd"]),
    }


class ReconciliationPipelineService:
    """Hexagonal application service orchestrating ingestion, reconciliation, and reset."""

    def __init__(
        self,
        lake: DataLakePort,
        warehouse: WarehousePort | None = None,
        engine: PolarsReconciliationEngine | None = None,
    ) -> None:
        self.lake = lake
        self.warehouse = warehouse
        self.engine = engine or PolarsReconciliationEngine()

    def ingest_and_reconcile_batch(
        self,
        raw_records: list[dict[str, object]],
        batch_id: str,
        valuation_date: str = "2026-11-10",
        max_rejection_ratio: float = 1.0,
    ) -> dict[str, object]:
        """Execute end-to-end batch customs validation, ZSTD Parquet write, and NAV audit."""
        t0 = time.perf_counter()
        self.lake.ensure_buckets_exist()
        if self.warehouse is not None:
            self.warehouse.initialize_schema()

        landing_uri = self.lake.write_landing_batch(batch_id, raw_records, valuation_date)
        clean_df, quarantined, report = audit_and_bifurcate_batch(
            raw_records, batch_id=batch_id, max_rejection_ratio=max_rejection_ratio
        )

        quarantine_meta: dict[str, object] | None = None
        if quarantined:
            quarantine_meta = self.lake.write_quarantine_artifacts(
                batch_id=batch_id,
                quarantined=quarantined,
                report=report,
                valuation_date=valuation_date,
                source_uri=landing_uri,
            )
            if self.warehouse is not None:
                self.warehouse.insert_quarantine_records(quarantined)

        validated_meta: dict[str, object] | None = None
        if clean_df.height > 0:
            validated_meta = self.lake.write_validated_parquet(
                batch_id=batch_id, clean_df=clean_df, valuation_date=valuation_date
            )

        reconciled_df = self.engine.execute_streaming_reconciliation(clean_df.lazy())
        if self.warehouse is not None and reconciled_df.height > 0:
            self.warehouse.upsert_reconciled_positions(reconciled_df, batch_id=batch_id)

        rollup_df = self.engine.compute_portfolio_rollup(reconciled_df)
        counts = _summarize_reconciled_df(reconciled_df)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        return {
            "status": "[OK]",
            "batch_id": batch_id,
            "valuation_date": valuation_date,
            "total_received": len(raw_records),
            "promoted_count": clean_df.height,
            "quarantined_count": len(quarantined),
            "reconciled_count": counts["reconciled_count"],
            "minor_tolerance_count": counts["minor_tolerance_count"],
            "nav_break_count": counts["nav_break_count"],
            "ibor_aum_usd": counts["ibor_aum_usd"],
            "custodian_aum_usd": counts["custodian_aum_usd"],
            "gross_nav_variance_usd": counts["gross_nav_variance_usd"],
            "net_nav_variance_usd": counts["net_nav_variance_usd"],
            "landing_uri": landing_uri,
            "validated_parquet": validated_meta,
            "quarantine_artifacts": quarantine_meta,
            "invariant_report": report.to_dict(),
            "portfolio_rollup": rollup_df.to_dicts(),
            "latency_ms": elapsed_ms,
        }

    def run_full_lake_reconciliation(
        self,
        valuation_date: str | None = None,
    ) -> dict[str, object]:
        """Lazily scan all validated S3 Parquet objects and execute streaming reconciliation."""
        t0 = time.perf_counter()
        validated_lf = self.lake.scan_validated_parquet(valuation_date=valuation_date)
        reconciled_df = self.engine.execute_streaming_reconciliation(validated_lf)
        if self.warehouse is not None and reconciled_df.height > 0:
            self.warehouse.upsert_reconciled_positions(reconciled_df, batch_id="LAKE-RESCAN")
        rollup_df = self.engine.compute_portfolio_rollup(reconciled_df)
        counts = _summarize_reconciled_df(reconciled_df)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "status": "[OK]",
            "total_reconciled_positions": reconciled_df.height,
            **counts,
            "portfolio_rollup": rollup_df.to_dicts(),
            "latency_ms": elapsed_ms,
        }

    def reset_entire_platform_to_zero(self) -> dict[str, object]:
        """Purge S3 Data Lake and Analytical Warehouse back to Honest Zero Baseline ($0.00)."""
        self.lake.reset_lake_to_zero()
        if self.warehouse is not None:
            self.warehouse.reset_warehouse_to_zero()
        zero_kpis = ExecutiveKpiSummary.zero_baseline()
        return {
            "status": "[OK]",
            "message": "[OK] Platform reset to Honest Zero Baseline ($0.00)",
            "reset_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kpis": zero_kpis.to_dict(),
            "lake_metrics": self.lake.get_lake_storage_metrics(),
        }
