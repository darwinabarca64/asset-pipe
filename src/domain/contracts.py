"""Pydantic v2 Core Ingestion Contracts, Alias Normalization, and Batch Customs Gate.

Enforces structural schema invariants, ISO 6166 Luhn Mod-10 ISIN validation,
ISO-4217 currency standards, strict IEEE 754 NaN/Inf rejection across core and
polymorphic client fields, and deterministic quarantine routing.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.domain.entities import (
    QuarantineRecordEntity,
    validate_isin_iso6166,
    validate_iso4217_currency,
)
from src.domain.exceptions import AssetPipeDomainError, CurrencyStandardError, ISINChecksumError

CANONICAL_COLUMN_ALIASES: dict[str, str] = {
    "pos_id": "position_id", "id": "position_id", "trade_id": "position_id",
    "fund_id": "portfolio_id", "portfolio": "portfolio_id", "account_id": "portfolio_id",
    "security_isin": "isin", "isin_code": "isin",
    "val_date": "valuation_date", "as_of_date": "valuation_date", "nav_date": "valuation_date",
    "ccy": "currency", "curr": "currency", "local_currency": "currency",
    "quantity": "units", "qty": "units", "shares": "units", "par_value": "units",
    "market_price": "price", "clean_price": "price", "unit_price": "price",
    "fx": "fx_rate", "spot_fx": "fx_rate", "exchange_rate": "fx_rate",
    "abor_nav": "custodian_reported_nav", "reported_nav": "custodian_reported_nav",
    "custodian_nav": "custodian_reported_nav", "bank_nav": "custodian_reported_nav",
}


def normalize_client_record_aliases(raw: dict[str, object]) -> dict[str, object]:
    """Map known custodian and client column aliases to canonical attribute names."""
    normalized: dict[str, object] = {}
    for key, value in raw.items():
        lookup = key.strip().lower() if isinstance(key, str) else ""
        canonical = CANONICAL_COLUMN_ALIASES.get(lookup)
        if canonical and canonical not in raw and canonical not in normalized:
            normalized[canonical] = value
        else:
            normalized[key] = value
    return normalized


class FinancialPositionContract(BaseModel):
    """Immutable Pydantic v2 contract for institutional position ingestion."""

    model_config = ConfigDict(extra="allow", frozen=True, str_strip_whitespace=True)

    position_id: str = Field(..., min_length=2, max_length=64)
    portfolio_id: str = Field(..., min_length=2, max_length=48)
    isin: str = Field(..., min_length=12, max_length=12)
    valuation_date: date
    currency: str = Field(..., min_length=3, max_length=3)
    units: float = Field(..., allow_inf_nan=False)
    price: float = Field(..., ge=0.0, allow_inf_nan=False)
    fx_rate: float = Field(..., gt=0.0, allow_inf_nan=False)
    custodian_reported_nav: float = Field(..., allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _apply_aliases_and_control_check(cls, data: object) -> object:
        """Normalize client column aliases and check raw identifier control chars."""
        if not isinstance(data, dict):
            return data
        mapped = normalize_client_record_aliases(data)
        for id_field in ("position_id", "portfolio_id"):
            raw_val = mapped.get(id_field)
            if isinstance(raw_val, str) and any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_val):
                raise ValueError(f"[CRITICAL] Control characters forbidden in '{id_field}'")
        return mapped

    @field_validator("position_id", "portfolio_id")
    @classmethod
    def _normalize_identifiers(cls, v: str) -> str:
        """Normalize identifiers to uppercase and reject ASCII control characters."""
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
            raise ValueError("[CRITICAL] Control characters forbidden in identifier")
        return v.upper()

    @field_validator("isin")
    @classmethod
    def _validate_isin(cls, v: str) -> str:
        """Validate ISIN structure, country prefix, and Luhn Mod-10 check digit."""
        return validate_isin_iso6166(v)

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, v: str) -> str:
        """Validate currency symbol against the ISO-4217 institutional catalog."""
        return validate_iso4217_currency(v)

    @model_validator(mode="after")
    def _validate_cross_field_and_extra_invariants(self) -> Self:
        """Enforce USD base FX identity and sanitize extra fields against NaN/Inf."""
        if self.currency == "USD" and abs(self.fx_rate - 1.0) > 1e-6:
            raise ValueError("[CRITICAL] Base currency USD requires fx_rate == 1.0")
        if self.model_extra:
            for k, val in self.model_extra.items():
                if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                    raise ValueError(f"[CRITICAL] IEEE 754 NaN/Inf detected in extra field '{k}'")
        return self

    def to_canonical_dict(self) -> dict[str, object]:
        """Return canonical 9-column dictionary plus isolated client extra_metadata."""
        extra = dict(self.model_extra) if self.model_extra else {}
        return {
            "position_id": self.position_id,
            "portfolio_id": self.portfolio_id,
            "isin": self.isin,
            "valuation_date": self.valuation_date.isoformat(),
            "currency": self.currency,
            "units": self.units,
            "price": self.price,
            "fx_rate": self.fx_rate,
            "custodian_reported_nav": self.custodian_reported_nav,
            "extra_metadata": extra,
        }


def _sanitize_for_json(obj: object) -> object:
    """Recursively convert IEEE 754 non-finite floats to safe string representations."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(item) for item in obj]
    return obj


def _classify_contract_failure(
    exc: ValidationError | AssetPipeDomainError,
) -> tuple[str, str, str]:
    """Deterministically map a validation exception to (rule_id, violation_code, msg)."""
    if isinstance(exc, ISINChecksumError):
        return ("GX-03: ISIN_ISO6166_REGEX", exc.code, exc.message)
    if isinstance(exc, CurrencyStandardError):
        return ("GX-04: CURRENCY_ISO4217", exc.code, exc.message)
    if isinstance(exc, AssetPipeDomainError):
        return ("GX-02: CORE_NOT_NULL", exc.code, exc.message)

    first_err = exc.errors()[0] if exc.errors() else {}
    loc_parts = {str(part) for part in first_err.get("loc", ())}
    msg = str(first_err.get("msg", str(exc)))
    msg_lower = msg.lower()
    is_non_finite = any(tok in msg_lower for tok in ("finite", "nan", "inf"))

    if "isin" in loc_parts:
        return ("GX-03: ISIN_ISO6166_REGEX", "INVALID_ISIN_ISO6166", msg)
    if "currency" in loc_parts:
        return ("GX-04: CURRENCY_ISO4217", "UNSUPPORTED_ISO4217_CURRENCY", msg)
    if "price" in loc_parts:
        code = "IEEE754_NON_FINITE_NUMBER" if is_non_finite else "CONTRACT_VIOLATION"
        return ("GX-05: PRICE_NON_NEGATIVE", code, msg)
    if "fx_rate" in loc_parts or "fx_rate" in msg_lower:
        code = "IEEE754_NON_FINITE_NUMBER" if is_non_finite else "CONTRACT_VIOLATION"
        return ("GX-06: FX_STRICT_POSITIVE", code, msg)

    code = "IEEE754_NON_FINITE_NUMBER" if is_non_finite else "CONTRACT_VIOLATION"
    return ("GX-02: CORE_NOT_NULL", code, msg)


def validate_position_records_batch(
    raw_records: list[dict[str, object]],
    batch_id: str,
) -> tuple[list[FinancialPositionContract], list[QuarantineRecordEntity]]:
    """Validate a batch of raw records, bifurcating into valid contracts and quarantine."""
    valid_contracts: list[FinancialPositionContract] = []
    quarantine_records: list[QuarantineRecordEntity] = []
    now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    for rec in raw_records:
        try:
            valid_contracts.append(FinancialPositionContract.model_validate(rec))
        except (ValidationError, AssetPipeDomainError) as exc:
            mapped = normalize_client_record_aliases(rec) if isinstance(rec, dict) else {}
            rule_id, violation_code, diag_msg = _classify_contract_failure(exc)
            safe_payload = json.dumps(
                _sanitize_for_json(rec), default=str, sort_keys=True, allow_nan=False
            )
            quarantine_records.append(
                QuarantineRecordEntity(
                    batch_id=batch_id,
                    position_id=str(mapped.get("position_id") or "UNKNOWN_POS"),
                    portfolio_id=str(mapped.get("portfolio_id") or "UNKNOWN_PORTFOLIO"),
                    isin=str(mapped.get("isin") or "UNKNOWN_ISIN"),
                    valuation_date=str(mapped.get("valuation_date") or "1970-01-01"),
                    rule_id=rule_id,
                    violation_code=violation_code,
                    diagnostic_message=diag_msg,
                    raw_payload_json=safe_payload,
                    quarantined_at_utc=now_utc,
                )
            )
    return valid_contracts, quarantine_records
