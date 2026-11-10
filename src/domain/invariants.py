"""Declarative Accounting Invariants (GX-01..GX-07) and Polars Vectorized Customs Gate.

Executes the Great Expectations style invariant suite in vectorized Polars Rust,
isolates compound primary key duplicates (GX-01), flags pre-reconciliation NAV
discrepancies (GX-07), and enforces the batch Quarantine Circuit Breaker.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import polars as pl

from src.domain.contracts import validate_position_records_batch
from src.domain.entities import ISO_4217_CURRENCIES, QuarantineRecordEntity
from src.domain.exceptions import QuarantineCircuitBreakerError

CORE_CANONICAL_COLUMNS: tuple[str, ...] = (
    "position_id", "portfolio_id", "isin", "valuation_date", "currency",
    "units", "price", "fx_rate", "custodian_reported_nav",
)

CANONICAL_POLARS_SCHEMA: dict[str, pl.DataType] = {
    "position_id": pl.String, "portfolio_id": pl.String, "isin": pl.String,
    "valuation_date": pl.String, "currency": pl.String, "units": pl.Float64,
    "price": pl.Float64, "fx_rate": pl.Float64, "custodian_reported_nav": pl.Float64,
    "extra_metadata_json": pl.String,
}

_ISIN_REGEX_PATTERN = r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"


@dataclass(frozen=True, slots=True)
class ExpectationResult:
    """Immutable evaluation outcome for a single GX accounting invariant."""

    rule_id: str
    expectation_name: str
    passed: bool
    evaluated_count: int
    failed_count: int
    diagnostic: str

    def to_dict(self) -> dict[str, object]:
        """Serialize expectation result to a plain dictionary."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BatchInvariantReport:
    """Immutable forensic manifest summarizing GX-01..GX-07 batch evaluation."""

    batch_id: str
    total_received: int
    valid_promoted_count: int
    quarantined_count: int
    nav_break_detected_count: int
    rejection_rate_bps: float
    expectations: tuple[ExpectationResult, ...]

    @property
    def nav_break_Detected_count(self) -> int:
        """Alias preserving camel-case compatibility with audit callers."""
        return self.nav_break_detected_count

    def to_dict(self) -> dict[str, object]:
        """Serialize batch invariant report for .manifest.json persistence."""
        return {
            "batch_id": self.batch_id,
            "total_received": self.total_received,
            "valid_promoted_count": self.valid_promoted_count,
            "quarantined_count": self.quarantined_count,
            "nav_break_detected_count": self.nav_break_detected_count,
            "rejection_rate_bps": self.rejection_rate_bps,
            "expectations": [exp.to_dict() for exp in self.expectations],
        }


class FinancialExpectationSuite:
    """Vectorized Polars evaluation of the 7 institutional GX invariants."""

    @staticmethod
    def evaluate(
        df: pl.DataFrame,
        batch_id: str,
    ) -> tuple[pl.DataFrame, list[QuarantineRecordEntity], tuple[ExpectationResult, ...], int]:
        """Run GX-01..GX-07 on a promoted Polars DataFrame and isolate PK duplicates."""
        initial_rows = df.height
        now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        dup_quarantine: list[QuarantineRecordEntity] = []

        if initial_rows > 0:
            dup_mask = pl.struct(["portfolio_id", "isin", "valuation_date"]).is_duplicated()
            dup_df = df.filter(dup_mask)
            clean_df = df.filter(~dup_mask)
            for row_dict in dup_df.to_dicts():
                dup_quarantine.append(
                    QuarantineRecordEntity(
                        batch_id=batch_id,
                        position_id=str(row_dict.get("position_id", "UNKNOWN_POS")),
                        portfolio_id=str(row_dict.get("portfolio_id", "UNKNOWN_PORTFOLIO")),
                        isin=str(row_dict.get("isin", "UNKNOWN_ISIN")),
                        valuation_date=str(row_dict.get("valuation_date", "1970-01-01")),
                        rule_id="GX-01: PK_UNIQUENESS",
                        violation_code="COMPOUND_KEY_DUPLICATE_DETECTED",
                        diagnostic_message=(
                            "[CRITICAL] Duplicate compound key "
                            "(portfolio_id, isin, valuation_date)"
                        ),
                        raw_payload_json=json.dumps(row_dict, sort_keys=True, default=str),
                        quarantined_at_utc=now_utc,
                    )
                )
        else:
            clean_df = df

        clean_rows = clean_df.height
        if clean_rows > 0:
            calc_nav = pl.col("units") * pl.col("price") * pl.col("fx_rate")
            nav_break_abs = (calc_nav - pl.col("custodian_reported_nav")).abs()
            denom = (
                pl.when(pl.col("custodian_reported_nav").abs() > 0.0)
                .then(pl.col("custodian_reported_nav").abs())
                .when(calc_nav.abs() > 0.0)
                .then(calc_nav.abs())
                .otherwise(0.0)
            )
            nav_break_bps = (
                pl.when(denom > 0.0).then((nav_break_abs / denom) * 10000.0).otherwise(0.0)
            )
            counts = (
                clean_df.lazy()
                .select(
                    gx02=pl.any_horizontal(
                        pl.col(c).is_null() for c in CORE_CANONICAL_COLUMNS
                    ).sum(),
                    gx03=(~pl.col("isin").str.contains(_ISIN_REGEX_PATTERN)).sum(),
                    gx04=(~pl.col("currency").is_in(list(ISO_4217_CURRENCIES))).sum(),
                    gx05=((pl.col("price") < 0.0) | (~pl.col("price").is_finite())).sum(),
                    gx06=((pl.col("fx_rate") <= 0.0) | (~pl.col("fx_rate").is_finite())).sum(),
                    gx07=((nav_break_abs > 10.0) | (nav_break_bps > 5.0)).sum(),
                )
                .collect()
                .row(0, named=True)
            )
            f_gx02, f_gx03, f_gx04 = int(counts["gx02"]), int(counts["gx03"]), int(counts["gx04"])
            f_gx05, f_gx06, f_gx07 = int(counts["gx05"]), int(counts["gx06"]), int(counts["gx07"])
        else:
            f_gx02 = f_gx03 = f_gx04 = f_gx05 = f_gx06 = f_gx07 = 0

        f_gx01 = len(dup_quarantine)
        n = clean_rows
        specs = (
            ("GX-01: PK_UNIQUENESS", "expect_compound_columns_to_be_unique", initial_rows, f_gx01),
            ("GX-02: CORE_NOT_NULL", "expect_column_values_to_not_be_null", n, f_gx02),
            ("GX-03: ISIN_ISO6166_REGEX", "expect_column_values_to_match_regex", n, f_gx03),
            ("GX-04: CURRENCY_ISO4217", "expect_column_values_to_be_in_set", n, f_gx04),
            ("GX-05: PRICE_NON_NEGATIVE", "expect_column_values_to_be_between", n, f_gx05),
            ("GX-06: FX_STRICT_POSITIVE", "expect_column_values_to_be_between", n, f_gx06),
            (
                "GX-07: NAV_EQUATION_MATCH",
                "expect_column_pair_nav_discrepancy_within_tolerance",
                n,
                f_gx07,
            ),
        )
        expectations = tuple(
            ExpectationResult(
                rule_id=r_id,
                expectation_name=exp_name,
                passed=(failed == 0),
                evaluated_count=eval_cnt,
                failed_count=failed,
                diagnostic=(
                    f"[OK] {r_id} passed ({eval_cnt} rows)"
                    if failed == 0
                    else f"[WARN] {r_id} detected {failed} violation(s)"
                ),
            )
            for r_id, exp_name, eval_cnt, failed in specs
        )
        return clean_df, dup_quarantine, expectations, f_gx07


def audit_and_bifurcate_batch(
    raw_records: list[dict[str, object]],
    batch_id: str,
    max_rejection_ratio: float = 1.0,
) -> tuple[pl.DataFrame, list[QuarantineRecordEntity], BatchInvariantReport]:
    """Execute dual-gate Pydantic v2 + Polars GX-01..GX-07 customs validation."""
    total_received = len(raw_records)
    valid_contracts, gate1_quarantine = validate_position_records_batch(raw_records, batch_id)

    rows: list[dict[str, object]] = []
    for contract in valid_contracts:
        row_data = contract.to_canonical_dict()
        extra = row_data.pop("extra_metadata", {})
        row_data["extra_metadata_json"] = json.dumps(extra, sort_keys=True, default=str)
        rows.append(row_data)

    initial_df = pl.DataFrame(rows, schema=CANONICAL_POLARS_SCHEMA)
    clean_df, gx01_quarantine, expectations, nav_breaks = FinancialExpectationSuite.evaluate(
        initial_df, batch_id
    )
    all_quarantine = gate1_quarantine + gx01_quarantine
    quarantined_count = len(all_quarantine)
    rejection_bps = (
        round((quarantined_count / total_received) * 10000.0, 2) if total_received > 0 else 0.0
    )

    report = BatchInvariantReport(
        batch_id=batch_id,
        total_received=total_received,
        valid_promoted_count=clean_df.height,
        quarantined_count=quarantined_count,
        nav_break_detected_count=nav_breaks,
        rejection_rate_bps=rejection_bps,
        expectations=expectations,
    )

    if total_received > 0 and (quarantined_count / total_received) > max_rejection_ratio:
        raise QuarantineCircuitBreakerError(
            f"[CRITICAL] Batch '{batch_id}' rejection ratio "
            f"{quarantined_count}/{total_received} exceeded {max_rejection_ratio:.2%}",
            context={"batch_id": batch_id, "report": report.to_dict()},
        )
    return clean_df, all_quarantine, report
