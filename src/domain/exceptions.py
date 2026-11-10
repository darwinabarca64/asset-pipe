"""Domain Exception Taxonomy for Forensic Financial Auditing.

Provides a structured hierarchy of domain exceptions carrying machine-readable
violation codes and structured diagnostic context dictionaries for quarantine
routing and regulatory audit trails.
"""

from __future__ import annotations


class AssetPipeDomainError(Exception):
    """Base exception for all domain-level invariant and contract failures."""

    DEFAULT_CODE: str = "ASSET_PIPE_DOMAIN_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        context: dict[str, object] | None = None,
    ) -> None:
        self.code: str = code or self.DEFAULT_CODE
        self.message: str = message
        self.context: dict[str, object] = dict(context) if context else {}
        super().__init__(f"[{self.code}] {self.message}")

    def to_dict(self) -> dict[str, object]:
        """Serialize exception details for forensic quarantine logs."""
        return {
            "error_type": self.__class__.__name__,
            "code": self.code,
            "message": self.message,
            "context": dict(self.context),
        }


class DataContractViolationError(AssetPipeDomainError):
    """Raised when an incoming position violates the structural data contract."""

    DEFAULT_CODE: str = "CONTRACT_VIOLATION"


class ISINChecksumError(DataContractViolationError):
    """Raised when an ISIN code fails ISO 6166 structure or Luhn Mod-10 checksum."""

    DEFAULT_CODE: str = "INVALID_ISIN_ISO6166"


class CurrencyStandardError(DataContractViolationError):
    """Raised when a currency code is not a valid institutional ISO-4217 symbol."""

    DEFAULT_CODE: str = "UNSUPPORTED_ISO4217_CURRENCY"


class NonFiniteNumberError(DataContractViolationError):
    """Raised when a financial quantity, price, or FX rate is NaN or Infinite."""

    DEFAULT_CODE: str = "IEEE754_NON_FINITE_NUMBER"


class AccountingInvariantError(AssetPipeDomainError):
    """Raised when a double-entry NAV or valuation invariant is breached."""

    DEFAULT_CODE: str = "ACCOUNTING_INVARIANT_BREACH"


class QuarantineCircuitBreakerError(AssetPipeDomainError):
    """Raised when batch rejection rates exceed the configured safety threshold."""

    DEFAULT_CODE: str = "QUARANTINE_CIRCUIT_BREAKER_TRIPPED"


class WarehouseAdapterError(AssetPipeDomainError):
    """Raised when analytical persistence fails in DuckDB or Snowflake."""

    DEFAULT_CODE: str = "WAREHOUSE_PERSISTENCE_ERROR"
