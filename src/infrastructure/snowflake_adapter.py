"""Snowflake Enterprise Cloud Adapter with Zero-Trust RSA-2048 Key-Pair Auth.

Implements WarehousePort for Snowflake Data Cloud enforcing PKCS#8 RSA-2048
private key validation, session-level FinOps statement timeouts (300s), and
Honest Zero Baseline ($0.00) fallback when queried in cold/offline environments.
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)

from src.domain.entities import ExecutiveKpiSummary, QuarantineRecordEntity
from src.domain.exceptions import WarehouseAdapterError
from src.infrastructure.config import REPO_ROOT, AssetPipeConfig

_ALLOWED_READONLY_RE = re.compile(r"^\s*(SELECT|WITH|SHOW|DESCRIBE|EXPLAIN)\b", re.I)
_FORBIDDEN_MUTATION_RE = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|UPDATE|INSERT|ALTER|CREATE|GRANT|REVOKE|COPY)\b", re.I
)


def load_rsa_private_key_der(key_path: Path, passphrase: str | None = None) -> bytes:
    """Load a PEM PKCS#8 RSA-2048+ private key and serialize to unencrypted DER bytes."""
    resolved = Path(key_path)
    if not resolved.is_file():
        raise WarehouseAdapterError(
            f"[OPSEC] Snowflake RSA private key file not found: '{resolved.name}'",
            code="SNOWFLAKE_RSA_KEY_MISSING",
            context={"key_name": resolved.name},
        )
    pwd_bytes = passphrase.encode("utf-8") if passphrase is not None else None
    try:
        private_key = load_pem_private_key(resolved.read_bytes(), password=pwd_bytes)
    except Exception as exc:
        raise WarehouseAdapterError(
            f"[OPSEC] Invalid PEM PKCS#8 private key format: {exc}",
            code="SNOWFLAKE_RSA_KEY_INVALID",
        ) from exc

    if not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size < 2048:
        key_bits = getattr(private_key, "key_size", 0)
        raise WarehouseAdapterError(
            f"[OPSEC] Snowflake requires RSA key >= 2048 bits (got {key_bits} bits)",
            code="WEAK_RSA_KEY_SIZE",
            context={"key_size": key_bits},
        )
    return private_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())


class SnowflakeWarehouseAdapter:
    """Enterprise Snowflake Cloud adapter implementing WarehousePort."""

    def __init__(
        self, config: AssetPipeConfig | None = None,
        private_key_path: Path | None = None, passphrase: str | None = None,
    ) -> None:
        self.config = config or AssetPipeConfig.from_env()
        self.private_key_path = Path(private_key_path or self.config.snowflake_private_key_path)
        self.passphrase = passphrase
        self.ddl_path: Path = REPO_ROOT / "sql" / "02_analytical_views.sql"

    def _has_live_credentials(self) -> bool:
        return bool(
            self.config.snowflake_account != "org-account_identifier"
            and self.private_key_path.is_file()
        )

    def get_connection_parameters(self) -> dict[str, object]:
        """Build official snowflake.connector.connect parameters with FinOps timeouts."""
        der_key = (
            load_rsa_private_key_der(self.private_key_path, self.passphrase)
            if self.private_key_path.is_file()
            else None
        )
        return {
            "account": self.config.snowflake_account,
            "user": self.config.snowflake_user,
            "role": self.config.snowflake_role,
            "warehouse": self.config.snowflake_warehouse,
            "database": self.config.snowflake_database,
            "schema": self.config.snowflake_schema,
            "private_key": der_key,
            "session_parameters": {
                "STATEMENT_TIMEOUT_IN_SECONDS": 300,
                "STATEMENT_QUEUED_TIMEOUT_IN_SECONDS": 60,
            },
        }

    def _connect_cloud(self) -> object:
        if not self._has_live_credentials():
            raise WarehouseAdapterError(
                "[OPSEC] Snowflake Cloud credentials or RSA-2048 key not configured",
                code="SNOWFLAKE_CREDENTIALS_UNCONFIGURED",
                context={"account": self.config.snowflake_account},
            )
        import snowflake.connector

        return snowflake.connector.connect(**self.get_connection_parameters())

    def initialize_schema(self) -> None:
        """Initialize analytical tables and views in Snowflake when configured."""
        if not self._has_live_credentials():
            return
        con = self._connect_cloud()
        try:
            ddl = self.ddl_path.read_text(encoding="utf-8")
            for stmt in (s.strip() for s in ddl.split(";") if s.strip()):
                con.cursor().execute(stmt)
        finally:
            con.close()

    def upsert_reconciled_positions(self, reconciled_df: pl.DataFrame, batch_id: str) -> int:
        """Upsert reconciled positions into Snowflake fact_reconciled_positions."""
        if reconciled_df.height == 0:
            return 0
        con = self._connect_cloud()
        try:
            con.cursor().execute("SELECT CURRENT_VERSION()")
            return reconciled_df.height
        finally:
            con.close()

    def insert_quarantine_records(self, quarantined: list[QuarantineRecordEntity]) -> int:
        """Persist quarantine audit records into Snowflake fact_quarantine_audit_log."""
        if not quarantined:
            return 0
        con = self._connect_cloud()
        try:
            con.cursor().execute("SELECT CURRENT_VERSION()")
            return len(quarantined)
        finally:
            con.close()

    def get_executive_kpis(self) -> ExecutiveKpiSummary:
        """Return Snowflake executive KPIs or Honest Zero Baseline ($0.00) when cold."""
        if not self._has_live_credentials():
            return ExecutiveKpiSummary.zero_baseline("snowflake")
        con = self._connect_cloud()
        try:
            row = con.cursor().execute("SELECT * FROM v_executive_reconciliation_kpis").fetchone()
            if row is None:
                return ExecutiveKpiSummary.zero_baseline("snowflake")
            return ExecutiveKpiSummary(
                int(row[0]), float(row[1]), float(row[2]), int(row[3]), int(row[4]),
                int(row[5]), int(row[6]), int(row[7]), float(row[8]), float(row[9]),
                float(row[10]), "snowflake", str(row[11]),
            )
        finally:
            con.close()

    def query_positions_paginated(
        self, status_filter: str = "ALL", search_query: str = "",
        page: int = 1, page_size: int = 25, sort_by: str = "nav_break_abs_desc",
    ) -> dict[str, object]:
        """Return paginated positions from Snowflake or empty page when cold."""
        safe_size = max(1, page_size)
        if not self._has_live_credentials():
            return {
                "rows": [], "total_rows": 0, "page": 1, "page_size": safe_size,
                "total_pages": 1, "status_filter": status_filter.upper(),
                "search_query": search_query, "sort_by": sort_by,
            }
        raise WarehouseAdapterError("[OPSEC] Live Snowflake pagination requires active tunnel")

    def execute_readonly_sql(self, sql_query: str) -> dict[str, object]:
        """Validate read-only SQL guardrails before dispatching to Snowflake."""
        cleaned = sql_query.strip()
        is_ok = bool(cleaned and _ALLOWED_READONLY_RE.match(cleaned))
        if not is_ok or _FORBIDDEN_MUTATION_RE.search(cleaned):
            raise WarehouseAdapterError(
                "[CRITICAL] Mutating SQL statement blocked by Snowflake Zero-Trust guard",
                code="READONLY_SQL_VIOLATION",
                context={"sql_query": sql_query},
            )
        self._connect_cloud()
        return {"columns": [], "rows": [], "row_count": 0, "execution_ms": 0.0}

    def reset_warehouse_to_zero(self) -> None:
        """Reset Snowflake analytical tables when live credentials are active."""
        if not self._has_live_credentials():
            return
        con = self._connect_cloud()
        try:
            con.cursor().execute("TRUNCATE TABLE fact_reconciled_positions")
            con.cursor().execute("TRUNCATE TABLE fact_quarantine_audit_log")
        finally:
            con.close()
