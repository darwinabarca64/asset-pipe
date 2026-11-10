"""Pytest Suite for Polars Vectorized GX-01..GX-07 Invariants and Circuit Breaker.

Verifies clean batch promotion, GX-01 compound primary key duplicate quarantine,
GX-07 declarative NAV break detection without row removal, QuarantineCircuitBreakerError
tripping above threshold, and empty/100%-quarantined typed DataFrame guarantees.
"""

from __future__ import annotations

import json

import pytest

from src.domain.exceptions import QuarantineCircuitBreakerError
from src.domain.invariants import (
    CANONICAL_POLARS_SCHEMA,
    audit_and_bifurcate_batch,
)


def _make_position(
    pos_id: str = "POS-001",
    portfolio_id: str = "PF-INST-01",
    isin: str = "US0378331005",
    val_date: str = "2026-11-10",
    currency: str = "USD",
    units: float = 1000.0,
    price: float = 220.0,
    fx_rate: float = 1.0,
    custodian_nav: float = 220000.0,
    **extra: object,
) -> dict[str, object]:
    """Create a synthetic raw position dictionary for GX customs gate testing."""
    base: dict[str, object] = {
        "position_id": pos_id,
        "portfolio_id": portfolio_id,
        "isin": isin,
        "valuation_date": val_date,
        "currency": currency,
        "units": units,
        "price": price,
        "fx_rate": fx_rate,
        "custodian_reported_nav": custodian_nav,
    }
    base.update(extra)
    return base


def test_clean_reconciled_batch_passes_all_gx01_to_gx07_invariants() -> None:
    """Verify a 100% clean batch passes GX-01..GX-07 with 0 failures and typed schema."""
    batch = [
        _make_position("POS-AAPL", "PF-ALPHA", "US0378331005", desk="NY-EQ"),
        _make_position(
            "POS-MSFT",
            "PF-ALPHA",
            "US5949181045",
            units=500.0,
            price=410.0,
            custodian_nav=205000.0,
        ),
        _make_position(
            "POS-SAN",
            "PF-ALPHA",
            "ES0113900J37",
            currency="EUR",
            units=10000.0,
            price=4.50,
            fx_rate=1.08,
            custodian_nav=48600.0,
        ),
    ]
    clean_df, quarantined, report = audit_and_bifurcate_batch(batch, batch_id="B-CLEAN-01")

    assert clean_df.height == 3
    assert list(clean_df.columns) == list(CANONICAL_POLARS_SCHEMA.keys())
    assert json.loads(clean_df["extra_metadata_json"][0]) == {"desk": "NY-EQ"}
    assert len(quarantined) == 0
    assert report.total_received == 3
    assert report.valid_promoted_count == 3
    assert report.quarantined_count == 0
    assert report.nav_break_detected_count == 0
    assert report.nav_break_Detected_count == 0
    assert report.rejection_rate_bps == 0.0
    assert len(report.expectations) == 7
    assert all(exp.passed and exp.failed_count == 0 for exp in report.expectations)


def test_gx01_compound_primary_key_duplicates_isolated_to_quarantine() -> None:
    """Verify duplicate (portfolio_id, isin, valuation_date) rows are quarantined by GX-01."""
    dup_a = _make_position("POS-DUP-1", "PF-CORE", "US0378331005", "2026-11-10")
    dup_b = _make_position("POS-DUP-2", "PF-CORE", "US0378331005", "2026-11-10")
    unique_pos = _make_position("POS-UNIQ", "PF-CORE", "DE000BAY0017", "2026-11-10")

    clean_df, quarantined, report = audit_and_bifurcate_batch(
        [dup_a, dup_b, unique_pos], batch_id="B-DUP-02"
    )

    assert clean_df.height == 1
    assert clean_df["position_id"][0] == "POS-UNIQ"
    assert len(quarantined) == 2
    assert all(q.rule_id == "GX-01: PK_UNIQUENESS" for q in quarantined)
    assert all(q.violation_code == "COMPOUND_KEY_DUPLICATE_DETECTED" for q in quarantined)

    gx01_res = report.expectations[0]
    assert gx01_res.rule_id == "GX-01: PK_UNIQUENESS"
    assert not gx01_res.passed
    assert gx01_res.failed_count == 2


def test_gx07_declarative_nav_break_flagged_but_retained_in_clean_dataframe() -> None:
    """Verify GX-07 detects a $5,000 NAV discrepancy while keeping the row in clean_df."""
    reconciled_row = _make_position("POS-OK", "PF-01", "US0378331005")
    nav_break_row = _make_position(
        "POS-BRK",
        "PF-01",
        "US5949181045",
        units=1000.0,
        price=100.0,
        fx_rate=1.0,
        custodian_nav=95000.0,
    )

    clean_df, quarantined, report = audit_and_bifurcate_batch(
        [reconciled_row, nav_break_row], batch_id="B-NAV-03"
    )

    assert len(quarantined) == 0
    assert clean_df.height == 2
    assert report.nav_break_detected_count == 1
    gx07_res = report.expectations[6]
    assert gx07_res.rule_id == "GX-07: NAV_EQUATION_MATCH"
    assert not gx07_res.passed
    assert gx07_res.failed_count == 1


def test_quarantine_circuit_breaker_trips_when_rejection_exceeds_threshold() -> None:
    """Verify 60% batch corruption with max_rejection_ratio=0.50 trips the circuit breaker."""
    batch = [
        _make_position("POS-OK-1", "PF-01", "US0378331005"),
        _make_position("POS-OK-2", "PF-01", "US5949181045"),
        _make_position("POS-BAD-1", "PF-01", "BAD_ISIN_001"),
        _make_position("POS-BAD-2", "PF-01", "US0378331005", price=-10.0),
        _make_position("POS-BAD-3", "PF-01", "US0378331005", units=float("nan")),
    ]
    with pytest.raises(QuarantineCircuitBreakerError) as exc_info:
        audit_and_bifurcate_batch(batch, batch_id="B-TRIP-04", max_rejection_ratio=0.50)

    err = exc_info.value
    assert err.code == "QUARANTINE_CIRCUIT_BREAKER_TRIPPED"
    assert err.context["batch_id"] == "B-TRIP-04"


def test_empty_and_all_quarantined_batches_return_typed_empty_dataframe() -> None:
    """Verify empty input or 100% quarantined input returns an empty typed pl.DataFrame."""
    empty_df, empty_q, empty_rep = audit_and_bifurcate_batch([], batch_id="B-EMPTY-05")
    assert empty_df.height == 0
    assert dict(empty_df.schema) == CANONICAL_POLARS_SCHEMA
    assert len(empty_q) == 0
    assert empty_rep.rejection_rate_bps == 0.0

    corrupt_batch = [_make_position("POS-CORRUPT", "PF-01", "INVALID_ISIN")]
    all_q_df, all_q_list, all_q_rep = audit_and_bifurcate_batch(
        corrupt_batch, batch_id="B-ALL-Q-06", max_rejection_ratio=1.0
    )
    assert all_q_df.height == 0
    assert dict(all_q_df.schema) == CANONICAL_POLARS_SCHEMA
    assert len(all_q_list) == 1
    assert all_q_rep.rejection_rate_bps == 10000.0
