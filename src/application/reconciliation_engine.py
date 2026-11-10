"""Polars Rust Vectorized NAV Reconciliation and 3-Factor Attribution Engine.

Executes lazy SIMD-vectorized Net Asset Value (NAV) calculations, basis-point
severity classification, trilinear variance attribution (Quantity, Price, FX),
and portfolio-level Net vs. Gross break rollups with zero Python row loops.
"""

from __future__ import annotations

import polars as pl

from src.domain.entities import BreakDriver, ReconciliationStatus
from src.domain.invariants import CANONICAL_POLARS_SCHEMA

RECONCILED_POLARS_SCHEMA: dict[str, pl.DataType] = {
    **CANONICAL_POLARS_SCHEMA,
    "calculated_nav": pl.Float64,
    "nav_break_signed": pl.Float64,
    "nav_break_abs": pl.Float64,
    "nav_break_bps": pl.Float64,
    "qty_effect_usd": pl.Float64,
    "price_effect_usd": pl.Float64,
    "fx_effect_usd": pl.Float64,
    "reconciliation_status": pl.String,
    "break_driver": pl.String,
}

PORTFOLIO_ROLLUP_SCHEMA: dict[str, pl.DataType] = {
    "portfolio_id": pl.String,
    "valuation_date": pl.String,
    "total_positions": pl.Int64,
    "ibor_aum_usd": pl.Float64,
    "custodian_aum_usd": pl.Float64,
    "net_fund_break_usd": pl.Float64,
    "gross_fund_break_usd": pl.Float64,
    "nav_break_count": pl.Int64,
}


class PolarsReconciliationEngine:
    """Stateless vectorized Polars engine for IBOR vs. Custodian ABOR reconciliation."""

    def reconcile_lazy(self, validated_lf: pl.LazyFrame) -> pl.LazyFrame:
        """Build a single lazy execution graph computing NAV breaks and 3-factor drivers."""
        abor_u = (
            pl.col("extra_metadata_json")
            .str.json_path_match("$.abor_units")
            .cast(pl.Float64, strict=False)
            .fill_null(pl.col("units"))
        )
        abor_p = (
            pl.col("extra_metadata_json")
            .str.json_path_match("$.abor_price")
            .cast(pl.Float64, strict=False)
            .fill_null(pl.col("price"))
        )
        abor_fx = (
            pl.col("extra_metadata_json")
            .str.json_path_match("$.abor_fx_rate")
            .cast(pl.Float64, strict=False)
            .fill_null(pl.col("fx_rate"))
        )

        with_nav = validated_lf.with_columns(
            calculated_nav=(pl.col("units") * pl.col("price") * pl.col("fx_rate")).round(4),
            _abor_u=abor_u,
            _abor_p=abor_p,
            _abor_fx=abor_fx,
        ).with_columns(
            nav_break_signed=(
                pl.col("calculated_nav") - pl.col("custodian_reported_nav")
            ).round(4),
            _raw_q=((pl.col("units") - pl.col("_abor_u")) * pl.col("_abor_p") * pl.col("_abor_fx"))
            .round(4),
            _raw_p=(
                pl.col("_abor_u") * (pl.col("price") - pl.col("_abor_p")) * pl.col("_abor_fx")
            ).round(4),
            _raw_fx=(
                pl.col("_abor_u") * pl.col("_abor_p") * (pl.col("fx_rate") - pl.col("_abor_fx"))
            ).round(4),
        )

        denom = pl.max_horizontal(
            pl.col("custodian_reported_nav").abs(),
            pl.col("calculated_nav").abs(),
            pl.lit(1e-9),
        )
        all_eff_zero = (
            (pl.col("_raw_q").abs() <= 1e-6)
            & (pl.col("_raw_p").abs() <= 1e-6)
            & (pl.col("_raw_fx").abs() <= 1e-6)
        )

        with_effects = with_nav.with_columns(
            nav_break_abs=pl.col("nav_break_signed").abs().round(4),
        ).with_columns(
            nav_break_bps=((pl.col("nav_break_abs") / denom) * 10000.0).round(4),
            qty_effect_usd=pl.col("_raw_q"),
            price_effect_usd=(
                pl.when(all_eff_zero & (pl.col("nav_break_abs") > 0.01))
                .then(pl.col("nav_break_signed"))
                .otherwise(pl.col("_raw_p"))
            ),
            fx_effect_usd=pl.col("_raw_fx"),
        )

        status_expr = (
            pl.when((pl.col("nav_break_abs") <= 0.01) | (pl.col("nav_break_bps") <= 0.1))
            .then(pl.lit(ReconciliationStatus.RECONCILED.value))
            .when((pl.col("nav_break_abs") <= 10.00) & (pl.col("nav_break_bps") <= 5.0))
            .then(pl.lit(ReconciliationStatus.MINOR_TOLERANCE.value))
            .otherwise(pl.lit(ReconciliationStatus.NAV_BREAK.value))
        )

        q_abs = pl.col("qty_effect_usd").abs()
        p_abs = pl.col("price_effect_usd").abs()
        fx_abs = pl.col("fx_effect_usd").abs()

        driver_expr = (
            pl.when(
                pl.col("reconciliation_status").is_in(
                    [
                        ReconciliationStatus.RECONCILED.value,
                        ReconciliationStatus.MINOR_TOLERANCE.value,
                    ]
                )
            )
            .then(pl.lit(BreakDriver.NONE.value))
            .when((q_abs >= p_abs) & (q_abs >= fx_abs) & (q_abs > 0.01))
            .then(pl.lit(BreakDriver.QUANTITY_BREAK.value))
            .when((fx_abs >= q_abs) & (fx_abs >= p_abs) & (fx_abs > 0.01))
            .then(pl.lit(BreakDriver.FX_BREAK.value))
            .otherwise(pl.lit(BreakDriver.PRICE_BREAK.value))
        )

        return (
            with_effects.with_columns(reconciliation_status=status_expr)
            .with_columns(break_driver=driver_expr)
            .select(list(RECONCILED_POLARS_SCHEMA.keys()))
        )

    def execute_streaming_reconciliation(self, validated_lf: pl.LazyFrame) -> pl.DataFrame:
        """Materialize the lazy reconciliation graph using Polars streaming engine."""
        lazy_graph = self.reconcile_lazy(validated_lf)
        df = lazy_graph.collect(engine="streaming")
        if df.height == 0:
            return pl.DataFrame([], schema=RECONCILED_POLARS_SCHEMA)
        return df

    def compute_portfolio_rollup(self, reconciled_df: pl.DataFrame) -> pl.DataFrame:
        """Aggregate Net vs. Gross NAV breaks and AUM by (portfolio_id, valuation_date)."""
        if reconciled_df.height == 0:
            return pl.DataFrame([], schema=PORTFOLIO_ROLLUP_SCHEMA)
        return (
            reconciled_df.lazy()
            .group_by(["portfolio_id", "valuation_date"])
            .agg(
                total_positions=pl.len().cast(pl.Int64),
                ibor_aum_usd=pl.col("calculated_nav").sum().round(4),
                custodian_aum_usd=pl.col("custodian_reported_nav").sum().round(4),
                net_fund_break_usd=pl.col("nav_break_signed").sum().round(4),
                gross_fund_break_usd=pl.col("nav_break_abs").sum().round(4),
                nav_break_count=(
                    pl.col("reconciliation_status") == ReconciliationStatus.NAV_BREAK.value
                )
                .sum()
                .cast(pl.Int64),
            )
            .sort(["portfolio_id", "valuation_date"])
            .collect()
        )
