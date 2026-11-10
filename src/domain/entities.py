"""Pure Domain Entities, Catalogs, and ISO 6166 Luhn Mod-10 ISIN Mathematics.

Defines immutable value objects, reconciliation states, break attribution drivers,
ISO-4217 currency validation, and the complete Base-36 Luhn Mod-10 checksum
algorithm for institutional securities identification.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum

from src.domain.exceptions import CurrencyStandardError, ISINChecksumError

ISO_4217_CURRENCIES: frozenset[str] = frozenset(
    {
        "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SEK", "NOK",
        "DKK", "SGD", "HKD", "CNY", "BRL", "MXN", "INR", "KRW", "ZAR", "PLN",
    }
)

VALID_ISIN_COUNTRY_PREFIXES: frozenset[str] = frozenset(
    {
        "XS", "EU", "US", "ES", "GB", "DE", "FR", "IT", "NL", "LU", "IE", "CH",
        "JP", "CA", "AU", "HK", "SG", "SE", "NO", "DK", "BE", "AT", "FI", "PT",
        "BR", "MX", "CN", "KR", "IN", "ZA", "KY", "BM", "VG", "JE", "GG",
    }
)

_ISIN_BODY_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}$")
_ISIN_FULL_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class ReconciliationStatus(str, Enum):
    """Tri-state and exception lifecycle statuses for position reconciliation."""

    RECONCILED = "RECONCILED"
    MINOR_TOLERANCE = "MINOR_TOLERANCE"
    NAV_BREAK = "NAV_BREAK"
    ORPHAN_IBOR = "ORPHAN_IBOR"
    ORPHAN_CUSTODIAN = "ORPHAN_CUSTODIAN"
    SCHEMA_REJECTED = "SCHEMA_REJECTED"


class BreakDriver(str, Enum):
    """Primary attribution factor explaining a Net Asset Value (NAV) discrepancy."""

    NONE = "NONE"
    QUANTITY_BREAK = "QUANTITY_BREAK"
    PRICE_BREAK = "PRICE_BREAK"
    FX_BREAK = "FX_BREAK"
    MULTI_FACTOR = "MULTI_FACTOR"
    ORPHAN_UNMATCHED = "ORPHAN_UNMATCHED"
    SCHEMA_CORRUPTION = "SCHEMA_CORRUPTION"


def compute_isin_check_digit(isin_body_11: str) -> str:
    """Compute the ISO 6166 Luhn Mod-10 check digit for an 11-character ISIN stem."""
    if not isinstance(isin_body_11, str):
        raise ISINChecksumError(
            "ISIN stem must be a string",
            context={"isin_body_11": repr(isin_body_11)},
        )
    stem = isin_body_11.strip().upper()
    if len(stem) != 11 or not _ISIN_BODY_RE.match(stem):
        raise ISINChecksumError(
            f"[CRITICAL] Invalid 11-character ISIN stem format: '{isin_body_11}'",
            context={"stem": stem},
        )
    expanded_digits = "".join(
        str(ord(ch) - 55) if "A" <= ch <= "Z" else ch for ch in stem
    )
    total = 0
    for idx, digit_char in enumerate(reversed(expanded_digits)):
        val = int(digit_char)
        if idx % 2 == 0:
            doubled = val * 2
            total += (doubled // 10) + (doubled % 10)
        else:
            total += val
    return str((10 - (total % 10)) % 10)


def validate_isin_iso6166(raw_isin: str) -> str:
    """Validate an ISIN code against ISO 6166 structure, country prefix, and Luhn Mod-10."""
    if not isinstance(raw_isin, str):
        raise ISINChecksumError(
            "ISIN value must be a string",
            context={"raw_isin": repr(raw_isin)},
        )
    cleaned = raw_isin.strip().upper()
    if len(cleaned) != 12 or not _ISIN_FULL_RE.match(cleaned):
        raise ISINChecksumError(
            f"[CRITICAL] ISIN '{raw_isin}' fails 12-character ISO 6166 structural format",
            context={"raw_isin": raw_isin, "normalized": cleaned, "length": len(cleaned)},
        )
    prefix = cleaned[:2]
    if prefix not in VALID_ISIN_COUNTRY_PREFIXES:
        raise ISINChecksumError(
            f"[CRITICAL] ISIN '{cleaned}' uses unsupported country prefix '{prefix}'",
            context={"raw_isin": raw_isin, "prefix": prefix},
        )
    expected_digit = compute_isin_check_digit(cleaned[:11])
    actual_digit = cleaned[11]
    if actual_digit != expected_digit:
        raise ISINChecksumError(
            f"[CRITICAL] ISIN '{cleaned}' failed Luhn Mod-10 check (expected {expected_digit})",
            context={
                "raw_isin": raw_isin,
                "expected_check_digit": expected_digit,
                "actual_check_digit": actual_digit,
            },
        )
    return cleaned


def validate_iso4217_currency(raw_currency: str) -> str:
    """Validate that a currency string is a canonical uppercase ISO-4217 symbol."""
    if not isinstance(raw_currency, str):
        raise CurrencyStandardError(
            "Currency code must be a string",
            context={"raw_currency": repr(raw_currency)},
        )
    cleaned = raw_currency.strip()
    if cleaned not in ISO_4217_CURRENCIES:
        raise CurrencyStandardError(
            f"[CRITICAL] Unsupported or non-canonical ISO-4217 currency: '{raw_currency}'",
            context={"raw_currency": raw_currency, "cleaned": cleaned},
        )
    return cleaned


@dataclass(frozen=True, slots=True)
class ReconciledPositionEntity:
    """Immutable audited position entity with NAV attribution decomposition."""

    position_id: str
    portfolio_id: str
    isin: str
    valuation_date: str
    currency: str
    units: float
    price: float
    fx_rate: float
    calculated_nav: float
    custodian_reported_nav: float
    nav_break_abs: float
    nav_break_bps: float
    reconciliation_status: ReconciliationStatus
    break_driver: BreakDriver
    qty_effect_usd: float = 0.0
    price_effect_usd: float = 0.0
    fx_effect_usd: float = 0.0
    extra_metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Convert entity to a plain serializable dictionary."""
        data = asdict(self)
        data["reconciliation_status"] = self.reconciliation_status.value
        data["break_driver"] = self.break_driver.value
        return data


@dataclass(frozen=True, slots=True)
class QuarantineRecordEntity:
    """Immutable forensic record for positions rejected at the ingestion customs gate."""

    batch_id: str
    position_id: str
    portfolio_id: str
    isin: str
    valuation_date: str
    rule_id: str
    violation_code: str
    diagnostic_message: str
    raw_payload_json: str
    quarantined_at_utc: str

    def to_dict(self) -> dict[str, object]:
        """Convert quarantine record to a plain serializable dictionary."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExecutiveKpiSummary:
    """Immutable executive summary for the 5-card institutional reconciliation console."""

    total_positions: int
    validated_ibor_aum_usd: float
    custodian_abor_aum_usd: float
    reconciled_ok_count: int
    minor_tolerance_count: int
    nav_break_count: int
    schema_rejected_count: int
    total_breaks_and_rejects: int
    gross_nav_variance_usd: float
    net_nav_variance_usd: float
    max_break_bps: float
    warehouse_engine: str = "duckdb"
    last_updated_utc: str = "1970-01-01T00:00:00Z"

    @property
    def quarantined_Read_count(self) -> int:
        """Alias for schema_rejected_count preserving dashboard contract compatibility."""
        return self.schema_rejected_count

    @classmethod
    def zero_baseline(cls, warehouse_engine: str = "duckdb") -> ExecutiveKpiSummary:
        """Return an Honest Zero Baseline state before any batch is ingested."""
        return cls(0, 0.0, 0.0, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0, warehouse_engine)

    def to_dict(self) -> dict[str, object]:
        """Convert executive KPI summary to a plain serializable dictionary."""
        return asdict(self)
