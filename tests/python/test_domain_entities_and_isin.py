"""Unit Tests for Pure Domain Entities, Exceptions, ISIN Luhn Mod-10, and ISO-4217.

Validates institutional ISIN checksums, deterministic error rejection, ISO-4217
currency enforcement, frozen entity immutability, and Honest Zero Baseline KPIs.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.domain.entities import (
    BreakDriver,
    ExecutiveKpiSummary,
    QuarantineRecordEntity,
    ReconciledPositionEntity,
    ReconciliationStatus,
    compute_isin_check_digit,
    validate_isin_iso6166,
    validate_iso4217_currency,
)
from src.domain.exceptions import (
    AccountingInvariantError,
    AssetPipeDomainError,
    CurrencyStandardError,
    DataContractViolationError,
    ISINChecksumError,
    NonFiniteNumberError,
    QuarantineCircuitBreakerError,
    WarehouseAdapterError,
)


@pytest.mark.parametrize(
    "valid_isin",
    [
        "US0378331005",  # Apple Inc.
        "US5949181045",  # Microsoft Corp.
        "ES0113900J37",  # Banco Santander S.A.
        "DE000BAY0017",  # Bayer AG
        "GB0002374006",  # Diageo plc
        " us0378331005 ",  # Whitespace and lowercase normalization check
    ],
)
def test_validate_real_institutional_isins_ok(valid_isin: str) -> None:
    """Verify real institutional ISIN codes pass ISO 6166 and Luhn Mod-10 checks."""
    normalized = validate_isin_iso6166(valid_isin)
    assert len(normalized) == 12
    assert normalized == valid_isin.strip().upper()


@pytest.mark.parametrize(
    ("invalid_isin", "reason"),
    [
        ("US0378331009", "altered check digit"),
        ("US0387331005", "internal character transposition 78 -> 87"),
        ("ES011390J037", "alphanumeric transposition 0J -> J0"),
        ("ZZ0378331005", "non-existent country prefix ZZ"),
        ("US037833100", "short length 11"),
        ("US03783310055", "long length 13"),
        ("120378331005", "numeric country prefix"),
        ("US037833100A", "non-numeric check digit"),
        ("", "empty string"),
    ],
)
def test_validate_isin_rejects_corrupted_codes(invalid_isin: str, reason: str) -> None:
    """Verify deterministic ISINChecksumError on corrupted or malformed ISINs."""
    with pytest.raises(ISINChecksumError) as exc_info:
        validate_isin_iso6166(invalid_isin)
    err = exc_info.value
    assert err.code == "INVALID_ISIN_ISO6166", f"Unexpected code for {reason}"
    assert "raw_isin" in err.context


def test_compute_isin_check_digit_synthetic_generation() -> None:
    """Verify deterministic ISIN generation from 11-character stems."""
    stems = [
        "US000000001",
        "LU123456789",
        "IE00B4L5Y98",
        "FR000012027",
        "XS201003999",
    ]
    for stem in stems:
        digit = compute_isin_check_digit(stem)
        assert digit.isdigit() and len(digit) == 1
        full_isin = f"{stem}{digit}"
        assert validate_isin_iso6166(full_isin) == full_isin

    with pytest.raises(ISINChecksumError):
        compute_isin_check_digit("INVALID")


@pytest.mark.parametrize("valid_ccy", ["USD", "EUR", "GBP", "JPY", "CHF", " USD "])
def test_validate_iso4217_currency_accepts_institutional_codes(valid_ccy: str) -> None:
    """Verify valid ISO-4217 institutional currencies pass validation."""
    assert validate_iso4217_currency(valid_ccy) == valid_ccy.strip()


@pytest.mark.parametrize("invalid_ccy", ["EURO", "$", "USDT", "GBp", "usd", "XXX", ""])
def test_validate_iso4217_currency_rejects_non_standard_codes(invalid_ccy: str) -> None:
    """Verify non-ISO symbols, crypto tickers, and pence subunits raise CurrencyStandardError."""
    with pytest.raises(CurrencyStandardError) as exc_info:
        validate_iso4217_currency(invalid_ccy)
    assert exc_info.value.code == "UNSUPPORTED_ISO4217_CURRENCY"


def test_executive_kpi_zero_baseline_and_immutability() -> None:
    """Verify Honest Zero Baseline KPIs are strictly zero and entities are frozen."""
    baseline = ExecutiveKpiSummary.zero_baseline(warehouse_engine="duckdb")
    assert baseline.total_positions == 0
    assert baseline.validated_ibor_aum_usd == 0.0
    assert baseline.custodian_abor_aum_usd == 0.0
    assert baseline.reconciled_ok_count == 0
    assert baseline.minor_tolerance_count == 0
    assert baseline.nav_break_count == 0
    assert baseline.schema_rejected_count == 0
    assert baseline.quarantined_Read_count == 0
    assert baseline.total_breaks_and_rejects == 0
    assert baseline.gross_nav_variance_usd == 0.0
    assert baseline.net_nav_variance_usd == 0.0
    assert baseline.max_break_bps == 0.0
    assert baseline.warehouse_engine == "duckdb"

    with pytest.raises(FrozenInstanceError):
        baseline.total_positions = 100  # type: ignore[misc]

    pos = ReconciledPositionEntity(
        position_id="POS-001",
        portfolio_id="PF-ALPHA",
        isin="US0378331005",
        valuation_date="2026-11-10",
        currency="USD",
        units=100.0,
        price=220.5,
        fx_rate=1.0,
        calculated_nav=22050.0,
        custodian_reported_nav=22050.0,
        nav_break_abs=0.0,
        nav_break_bps=0.0,
        reconciliation_status=ReconciliationStatus.RECONCILED,
        break_driver=BreakDriver.NONE,
    )
    with pytest.raises(FrozenInstanceError):
        pos.units = 200.0  # type: ignore[misc]
    assert pos.to_dict()["reconciliation_status"] == "RECONCILED"


def test_domain_exception_taxonomy_and_forensic_serialization() -> None:
    """Verify all domain exception subclasses preserve default codes and context."""
    cases: list[tuple[type[AssetPipeDomainError], str]] = [
        (DataContractViolationError, "CONTRACT_VIOLATION"),
        (ISINChecksumError, "INVALID_ISIN_ISO6166"),
        (CurrencyStandardError, "UNSUPPORTED_ISO4217_CURRENCY"),
        (NonFiniteNumberError, "IEEE754_NON_FINITE_NUMBER"),
        (AccountingInvariantError, "ACCOUNTING_INVARIANT_BREACH"),
        (QuarantineCircuitBreakerError, "QUARANTINE_CIRCUIT_BREAKER_TRIPPED"),
        (WarehouseAdapterError, "WAREHOUSE_PERSISTENCE_ERROR"),
    ]
    for exc_cls, expected_code in cases:
        err = exc_cls("Forensic test error", context={"field": "nav"})
        payload = err.to_dict()
        assert err.code == expected_code
        assert payload["code"] == expected_code
        assert payload["context"] == {"field": "nav"}

    q_rec = QuarantineRecordEntity(
        batch_id="B-01",
        position_id="POS-ERR",
        portfolio_id="PF-01",
        isin="BAD",
        valuation_date="2026-11-10",
        rule_id="GX-02",
        violation_code="INVALID_ISIN_ISO6166",
        diagnostic_message="[CRITICAL] Checksum mismatch",
        raw_payload_json="{}",
        quarantined_at_utc="2026-11-10T00:00:00Z",
    )
    assert q_rec.to_dict()["rule_id"] == "GX-02"
