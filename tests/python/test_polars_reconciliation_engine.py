"""Pytest Suite for Polars Vectorized Reconciliation Engine and Pipeline Service.

Validates cent-exact NAV mathematics, basis-point calculations, 3-factor break
attribution (QUANTITY_BREAK, PRICE_BREAK, FX_BREAK), Net vs. Gross rollup masking
prevention, SyntheticPortfolioGenerator, and ReconciliationPipelineService.
"""

from __future__ import annotations

from pathlib import Path

from src.application.ingestion_service import ReconciliationPipelineService
from src.application.reconciliation_engine import (
    PORTFOLIO_ROLLUP_SCHEMA,
    RECONCILED_POLARS_SCHEMA,
    PolarsReconciliationEngine,
)
from src.application.synthetic_generator import SyntheticPortfolioGenerator
from src.domain.invariants import CANONICAL_POLARS_SCHEMA, audit_and_bifurcate_batch
from src.infrastructure.config import AssetPipeConfig
from src.infrastructure.s3_parquet_adapter import S3ParquetAdapter


def test_cent_exact_nav_bps_and_three_factor_attribution_drivers() -> None:
    """Verify exact NAV, bps, and QUANTITY_BREAK, PRICE_BREAK, FX_BREAK attribution."""
    raw_positions: list[dict[str, object]] = [
        {
            "position_id": "POS-EXACT-OK",
            "portfolio_id": "BLK-ALADDIN-CORE",
            "isin": "US0378331005",
            "valuation_date": "2026-11-10",
            "currency": "USD",
            "units": 1000.0,
            "price": 225.00,
            "fx_rate": 1.0,
            "custodian_reported_nav": 225000.00,
            "abor_units": 1000.0,
            "abor_price": 225.00,
            "abor_fx_rate": 1.0,
        },
        {
            "position_id": "POS-MINOR-TOL",
            "portfolio_id": "BLK-ALADDIN-CORE",
            "isin": "ES0113900J37",
            "valuation_date": "2026-11-10",
            "currency": "EUR",
            "units": 10000.0,
            "price": 10.00,
            "fx_rate": 1.0800,
            "custodian_reported_nav": 107998.50,
            "abor_units": 10000.0,
            "abor_price": 10.00,
            "abor_fx_rate": 1.079985,
        },
        {
            "position_id": "POS-QTY-BRK",
            "portfolio_id": "BLK-ALADDIN-CORE",
            "isin": "US5949181045",
            "valuation_date": "2026-11-10",
            "currency": "USD",
            "units": 5000.0,
            "price": 100.00,
            "fx_rate": 1.0,
            "custodian_reported_nav": 420000.00,
            "abor_units": 4200.0,
            "abor_price": 100.00,
            "abor_fx_rate": 1.0,
        },
        {
            "position_id": "POS-PRICE-BRK",
            "portfolio_id": "BLK-ALADDIN-CORE",
            "isin": "DE000BAY0017",
            "valuation_date": "2026-11-10",
            "currency": "EUR",
            "units": 2000.0,
            "price": 105.00,
            "fx_rate": 1.10,
            "custodian_reported_nav": 209000.00,
            "abor_units": 2000.0,
            "abor_price": 95.00,
            "abor_fx_rate": 1.10,
        },
        {
            "position_id": "POS-FX-BRK",
            "portfolio_id": "BLK-ALADDIN-CORE",
            "isin": "GB0002374006",
            "valuation_date": "2026-11-10",
            "currency": "GBP",
            "units": 10000.0,
            "price": 50.00,
            "fx_rate": 1.3000,
            "custodian_reported_nav": 625000.00,
            "abor_units": 10000.0,
            "abor_price": 50.00,
            "abor_fx_rate": 1.2500,
        },
    ]

    clean_df, quarantined, _ = audit_and_bifurcate_batch(raw_positions, "B-ATTR-01")
    assert len(quarantined) == 0

    engine = PolarsReconciliationEngine()
    rec_df = engine.execute_streaming_reconciliation(clean_df.lazy())
    assert dict(rec_df.schema) == RECONCILED_POLARS_SCHEMA
    by_id = {row["position_id"]: row for row in rec_df.to_dicts()}

    assert by_id["POS-EXACT-OK"]["reconciliation_status"] == "RECONCILED"
    assert by_id["POS-EXACT-OK"]["break_driver"] == "NONE"
    assert by_id["POS-EXACT-OK"]["nav_break_abs"] == 0.0

    assert by_id["POS-MINOR-TOL"]["reconciliation_status"] == "MINOR_TOLERANCE"
    assert by_id["POS-MINOR-TOL"]["break_driver"] == "NONE"
    assert by_id["POS-MINOR-TOL"]["nav_break_abs"] == 1.50

    qty_row = by_id["POS-QTY-BRK"]
    assert qty_row["reconciliation_status"] == "NAV_BREAK"
    assert qty_row["break_driver"] == "QUANTITY_BREAK"
    assert qty_row["calculated_nav"] == 500000.00
    assert qty_row["nav_break_signed"] == 80000.00
    assert qty_row["qty_effect_usd"] == 80000.00
    assert qty_row["nav_break_bps"] == 1600.0

    price_row = by_id["POS-PRICE-BRK"]
    assert price_row["reconciliation_status"] == "NAV_BREAK"
    assert price_row["break_driver"] == "PRICE_BREAK"
    assert price_row["price_effect_usd"] == 22000.00

    fx_row = by_id["POS-FX-BRK"]
    assert fx_row["reconciliation_status"] == "NAV_BREAK"
    assert fx_row["break_driver"] == "FX_BREAK"
    assert fx_row["fx_effect_usd"] == 25000.00


def test_portfolio_rollup_exposes_offsetting_breaks_via_gross_variance() -> None:
    """Verify +$5,000 and -$5,000 breaks yield net $0.00 but gross $10,000.00."""
    offsetting = [
        {
            "position_id": "POS-PLUS-5K",
            "portfolio_id": "SANT-IB-RATES",
            "isin": "US0378331005",
            "valuation_date": "2026-11-10",
            "currency": "USD",
            "units": 1000.0,
            "price": 105.0,
            "fx_rate": 1.0,
            "custodian_reported_nav": 100000.0,
        },
        {
            "position_id": "POS-MINUS-5K",
            "portfolio_id": "SANT-IB-RATES",
            "isin": "US5949181045",
            "valuation_date": "2026-11-10",
            "currency": "USD",
            "units": 1000.0,
            "price": 95.0,
            "fx_rate": 1.0,
            "custodian_reported_nav": 100000.0,
        },
    ]
    clean_df, _, _ = audit_and_bifurcate_batch(offsetting, "B-OFFSET-02")
    engine = PolarsReconciliationEngine()
    rec_df = engine.execute_streaming_reconciliation(clean_df.lazy())
    rollup_df = engine.compute_portfolio_rollup(rec_df)

    assert dict(rollup_df.schema) == PORTFOLIO_ROLLUP_SCHEMA
    row = rollup_df.to_dicts()[0]
    assert row["portfolio_id"] == "SANT-IB-RATES"
    assert row["total_positions"] == 2
    assert row["net_fund_break_usd"] == 0.0
    assert row["gross_fund_break_usd"] == 10000.0
    assert row["nav_break_count"] == 2


def test_pipeline_service_end_to_end_clean_corrupt_and_zero_reset(tmp_path: Path) -> None:
    """Verify full lifecycle of ReconciliationPipelineService and synthetic generator."""
    cfg = AssetPipeConfig.from_env(local_lake_override=tmp_path / "lake")
    lake = S3ParquetAdapter(cfg)
    service = ReconciliationPipelineService(lake=lake)
    generator = SyntheticPortfolioGenerator()

    clean_batch = generator.generate_clean_institutional_batch(count=30, batch_seq=1)
    res_clean = service.ingest_and_reconcile_batch(clean_batch, batch_id="BATCH-CLEAN-30")
    assert res_clean["total_received"] == 30
    assert res_clean["promoted_count"] == 30
    assert res_clean["quarantined_count"] == 0
    assert res_clean["nav_break_count"] == 0
    assert int(res_clean["reconciled_count"]) + int(res_clean["minor_tolerance_count"]) == 30  # type: ignore[arg-type]

    stress_batch = generator.generate_corrupt_and_nav_break_batch(
        valid_with_breaks_count=12, schema_corrupt_count=6, batch_seq=2
    )
    res_stress = service.ingest_and_reconcile_batch(stress_batch, batch_id="BATCH-STRESS-18")
    assert res_stress["total_received"] == 18
    assert res_stress["promoted_count"] == 12
    assert res_stress["quarantined_count"] == 6
    assert res_stress["nav_break_count"] == 12

    full_scan_res = service.run_full_lake_reconciliation(valuation_date="2026-11-10")
    assert full_scan_res["total_reconciled_positions"] == 42
    assert full_scan_res["nav_break_count"] == 12

    reset_res = service.reset_entire_platform_to_zero()
    assert reset_res["status"] == "[OK]"
    assert reset_res["kpis"]["total_positions"] == 0  # type: ignore[index]
    assert reset_res["lake_metrics"]["total_objects"] == 0  # type: ignore[index]

    empty_lf = lake.scan_validated_parquet()
    assert dict(empty_lf.collect().schema) == CANONICAL_POLARS_SCHEMA
    assert service.engine.execute_streaming_reconciliation(empty_lf).height == 0
