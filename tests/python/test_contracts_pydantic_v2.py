"""Pytest Suite for Pydantic v2 Core Ingestion Contracts and Customs Gate.

Validates institutional position contracts, ConfigDict(extra="allow") polymorphism,
custodian alias normalization, strict IEEE 754 NaN/Inf blocking in core and extra
fields, USD FX identity invariant, and batch quarantine bifurcation.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from src.domain.contracts import (
    CANONICAL_COLUMN_ALIASES,
    FinancialPositionContract,
    normalize_client_record_aliases,
    validate_position_records_batch,
)


def _base_apple_record() -> dict[str, object]:
    """Return a valid baseline institutional position dictionary for Apple Inc."""
    return {
        "position_id": "pos-aapl-001",
        "portfolio_id": "pf-global-eq",
        "isin": "US0378331005",
        "valuation_date": "2026-11-10",
        "currency": "USD",
        "units": 1500.0,
        "price": 224.50,
        "fx_rate": 1.0,
        "custodian_reported_nav": 336750.0,
    }


def test_standard_institutional_positions_validation_ok() -> None:
    """Verify Apple (USD) and Santander (EUR, short units) pass Pydantic v2 validation."""
    aapl = FinancialPositionContract.model_validate(_base_apple_record())
    assert aapl.position_id == "POS-AAPL-001"
    assert aapl.portfolio_id == "PF-GLOBAL-EQ"
    assert aapl.isin == "US0378331005"
    assert aapl.valuation_date == date(2026, 11, 10)
    assert aapl.currency == "USD"
    assert aapl.fx_rate == 1.0

    san_raw = {
        "position_id": "POS-SAN-002",
        "portfolio_id": "PF-EURO-BANK",
        "isin": "ES0113900J37",
        "valuation_date": "2026-11-10",
        "currency": "EUR",
        "units": -2500.0,
        "price": 4.62,
        "fx_rate": 1.085,
        "custodian_reported_nav": -12531.75,
    }
    san = FinancialPositionContract.model_validate(san_raw)
    assert san.isin == "ES0113900J37"
    assert san.currency == "EUR"
    assert san.units == -2500.0
    assert san.fx_rate == 1.085
    with pytest.raises(ValidationError):
        san.price = 10.0  # type: ignore[misc]


def test_polymorphic_extra_allow_preserves_client_metadata() -> None:
    """Verify ConfigDict(extra='allow') retains client columns in model_extra and dict."""
    raw = _base_apple_record()
    raw.update(
        {
            "desk_code": "MADRID-RATES",
            "bloomberg_figi": "BBG000B9XRY4",
            "seniority_tranche": "SENIOR_SECURED",
        }
    )
    contract = FinancialPositionContract.model_validate(raw)
    assert contract.model_extra == {
        "desk_code": "MADRID-RATES",
        "bloomberg_figi": "BBG000B9XRY4",
        "seniority_tranche": "SENIOR_SECURED",
    }
    canonical = contract.to_canonical_dict()
    assert canonical["valuation_date"] == "2026-11-10"
    assert canonical["extra_metadata"] == {
        "desk_code": "MADRID-RATES",
        "bloomberg_figi": "BBG000B9XRY4",
        "seniority_tranche": "SENIOR_SECURED",
    }


def test_automatic_client_column_alias_normalization() -> None:
    """Verify custodian column aliases normalize cleanly to canonical schema fields."""
    aliased_payload: dict[str, object] = {
        "pos_id": "TRD-9901",
        "fund_id": "FUND-LUX-01",
        "security_isin": "DE000BAY0017",
        "as_of_date": "2026-11-10",
        "ccy": "EUR",
        "qty": 500.0,
        "market_price": 28.40,
        "spot_fx": 1.08,
        "abor_nav": 15336.0,
        "custodian_ticket": "TCK-77",
    }
    normalized = normalize_client_record_aliases(aliased_payload)
    assert normalized["portfolio_id"] == "FUND-LUX-01"
    assert normalized["units"] == 500.0
    assert "pos_id" in CANONICAL_COLUMN_ALIASES

    contract = FinancialPositionContract.model_validate(aliased_payload)
    assert contract.position_id == "TRD-9901"
    assert contract.portfolio_id == "FUND-LUX-01"
    assert contract.isin == "DE000BAY0017"
    assert contract.currency == "EUR"
    assert contract.units == 500.0
    assert contract.price == 28.40
    assert contract.fx_rate == 1.08
    assert contract.custodian_reported_nav == 15336.0
    assert contract.model_extra == {"custodian_ticket": "TCK-77"}


@pytest.mark.parametrize("bad_float", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize(
    "target_field",
    ["units", "price", "fx_rate", "custodian_reported_nav", "custom_beta"],
)
def test_strict_rejection_of_ieee754_nan_and_inf_in_core_and_extra_fields(
    bad_float: float,
    target_field: str,
) -> None:
    """Verify IEEE 754 NaN, +Inf, and -Inf are rejected in both core and extra fields."""
    raw = _base_apple_record()
    raw[target_field] = bad_float
    with pytest.raises(ValidationError) as exc_info:
        FinancialPositionContract.model_validate(raw)
    err_text = str(exc_info.value).lower()
    assert any(token in err_text for token in ("finite", "nan", "inf"))


def test_rejection_of_negative_price_non_positive_fx_and_usd_fx_mismatch() -> None:
    """Verify negative price, zero/negative FX rate, and USD fx_rate != 1.0 fail."""
    neg_price = _base_apple_record()
    neg_price["price"] = -5.0
    with pytest.raises(ValidationError):
        FinancialPositionContract.model_validate(neg_price)

    zero_fx = _base_apple_record()
    zero_fx["currency"] = "EUR"
    zero_fx["fx_rate"] = 0.0
    with pytest.raises(ValidationError):
        FinancialPositionContract.model_validate(zero_fx)

    neg_fx = _base_apple_record()
    neg_fx["currency"] = "GBP"
    neg_fx["fx_rate"] = -1.25
    with pytest.raises(ValidationError):
        FinancialPositionContract.model_validate(neg_fx)

    usd_bad_fx = _base_apple_record()
    usd_bad_fx["currency"] = "USD"
    usd_bad_fx["fx_rate"] = 1.15
    with pytest.raises(ValidationError, match="Base currency USD requires fx_rate == 1.0"):
        FinancialPositionContract.model_validate(usd_bad_fx)


def test_batch_customs_gate_bifurcates_valid_and_quarantine_records() -> None:
    """Verify a 5-record mixed batch yields 3 valid contracts and 2 forensic quarantines."""
    rec_valid_1 = _base_apple_record()
    rec_valid_2 = {
        **_base_apple_record(),
        "position_id": "POS-MSFT-002",
        "isin": "US5949181045",
    }
    rec_valid_3 = {
        **_base_apple_record(),
        "position_id": "POS-SAN-003",
        "isin": "ES0113900J37",
        "currency": "EUR",
        "fx_rate": 1.085,
    }
    rec_corrupt_isin = {
        **_base_apple_record(),
        "position_id": "POS-BAD-ISIN",
        "isin": "US0378331009",
    }
    rec_corrupt_nan = {
        **_base_apple_record(),
        "position_id": "POS-BAD-NAN",
        "price": float("nan"),
    }

    batch = [rec_valid_1, rec_corrupt_isin, rec_valid_2, rec_corrupt_nan, rec_valid_3]
    valid_list, quarantine_list = validate_position_records_batch(batch, batch_id="BATCH-003")

    assert len(valid_list) == 3
    assert [c.position_id for c in valid_list] == [
        "POS-AAPL-001",
        "POS-MSFT-002",
        "POS-SAN-003",
    ]

    assert len(quarantine_list) == 2
    q_isin, q_nan = quarantine_list[0], quarantine_list[1]
    assert q_isin.batch_id == "BATCH-003"
    assert q_isin.position_id == "POS-BAD-ISIN"
    assert q_isin.rule_id == "GX-03: ISIN_ISO6166_REGEX"
    assert q_isin.violation_code == "INVALID_ISIN_ISO6166"

    assert q_nan.position_id == "POS-BAD-NAN"
    assert q_nan.rule_id == "GX-05: PRICE_NON_NEGATIVE"
    assert q_nan.violation_code == "IEEE754_NON_FINITE_NUMBER"
    parsed_nan_json = json.loads(q_nan.raw_payload_json)
    assert parsed_nan_json["price"] == "nan"
