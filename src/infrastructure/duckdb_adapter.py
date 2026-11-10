"""DuckDB Local OLAP Adapter with Zero-Hold Write Lock and Arrow Zero-Copy.

Implements WarehousePort over DuckDB ensuring zero persistent file locks across
ports :3000 (Executive Console) and :4213 (SQL Workbench), Honest Zero Baseline
($0.00) KPIs, server-side pagination, and read-only SQL guardrails.
"""

from __future__ import annotations

import math
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import polars as pl

from src.domain.entities import ExecutiveKpiSummary, QuarantineRecordEntity
from src.domain.exceptions import WarehouseAdapterError
from src.infrastructure.config import REPO_ROOT, AssetPipeConfig

_ALLOWED_READONLY_RE = re.compile(r"^\s*(SELECT|WITH|SHOW|DESCRIBE|EXPLAIN|PRAGMA)\b", re.I)
_FORBIDDEN_MUTATION_RE = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|UPDATE|INSERT|ALTER|CREATE|ATTACH|COPY|DETACH|CALL)\b", re.I
)
_SORT_COLUMNS: dict[str, str] = {
    "nav_break_abs_desc": "nav_break_abs DESC, position_id ASC",
    "nav_break_bps_desc": "nav_break_bps DESC, position_id ASC",
    "calculated_nav_desc": "calculated_nav DESC, position_id ASC",
    "portfolio_asc": "portfolio_id ASC, isin ASC",
}
_ALL_POSITIONS_UNION_SQL = (
    "SELECT batch_id, position_id, portfolio_id, isin, valuation_date, currency, "
    "units, price, fx_rate, calculated_nav, custodian_reported_nav, nav_break_signed, "
    "nav_break_abs, nav_break_bps, reconciliation_status, break_driver, qty_effect_usd, "
    "price_effect_usd, fx_effect_usd, 'NONE' AS rule_id, '' AS diagnostic_message, "
    "updated_at_utc AS event_timestamp_utc FROM fact_reconciled_positions "
    "UNION ALL SELECT * FROM v_nav_and_schema_breaks_triage "
    "WHERE reconciliation_status = 'SCHEMA_REJECTED'"
)


class DuckDBWarehouseAdapter:
    """Zero-Hold Write Lock DuckDB adapter implementing WarehousePort."""

    def __init__(self, config: AssetPipeConfig | None = None, db_path: Path | None = None) -> None:
        self.config = config or AssetPipeConfig.from_env()
        self.db_path: Path = (db_path or self.config.duckdb_path).resolve()
        self.ddl_path: Path = REPO_ROOT / "sql" / "02_analytical_views.sql"

    def _connect_write(self) -> duckdb.DuckDBPyConnection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return duckdb.connect(str(self.db_path), read_only=False)

    def _connect_read(self) -> duckdb.DuckDBPyConnection:
        last_err: Exception | None = None
        for _ in range(5):
            try:
                return duckdb.connect(str(self.db_path), read_only=True)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(0.015)
        raise WarehouseAdapterError(
            f"[CRITICAL] Could not open DuckDB in read_only=True mode: {last_err}",
            context={"db_path": self.db_path.name},
        )

    def initialize_schema(self) -> None:
        """Execute sql/02_analytical_views.sql idempotently and release file lock."""
        con = self._connect_write()
        try:
            con.execute(self.ddl_path.read_text(encoding="utf-8"))
            con.execute("CHECKPOINT;")
        finally:
            con.close()

    def upsert_reconciled_positions(self, reconciled_df: pl.DataFrame, batch_id: str) -> int:
        """Upsert Polars DataFrame via Zero-Copy Arrow into fact_reconciled_positions."""
        if reconciled_df.height == 0:
            return 0
        if not self.db_path.is_file():
            self.initialize_schema()
        now_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        arrow_batch = reconciled_df.with_columns(
            batch_id=pl.lit(batch_id), updated_at_utc=pl.lit(now_utc)
        ).to_arrow()
        con = self._connect_write()
        try:
            con.register("arrow_batch", arrow_batch)
            con.execute(
                "INSERT OR REPLACE INTO fact_reconciled_positions SELECT "
                "position_id, portfolio_id, isin, valuation_date, currency, units, price, "
                "fx_rate, calculated_nav, custodian_reported_nav, nav_break_signed, "
                "nav_break_abs, nav_break_bps, reconciliation_status, break_driver, "
                "qty_effect_usd, price_effect_usd, fx_effect_usd, extra_metadata_json, "
                "batch_id, updated_at_utc FROM arrow_batch; CHECKPOINT;"
            )
            return reconciled_df.height
        finally:
            con.close()

    def insert_quarantine_records(self, quarantined: list[QuarantineRecordEntity]) -> int:
        """Insert deduplicated forensic quarantine records into fact_quarantine_audit_log."""
        if not quarantined:
            return 0
        if not self.db_path.is_file():
            self.initialize_schema()
        dedup = {
            (r.batch_id, r.position_id, r.rule_id): (
                r.batch_id, r.position_id, r.portfolio_id, r.isin, r.valuation_date,
                r.rule_id, r.violation_code, r.diagnostic_message,
                r.raw_payload_json, r.quarantined_at_utc,
            )
            for r in quarantined
        }
        rows_to_insert = list(dedup.values())
        con = self._connect_write()
        try:
            del_sql = (
                "DELETE FROM fact_quarantine_audit_log "
                "WHERE batch_id = ? AND position_id = ? AND rule_id = ?"
            )
            con.executemany(del_sql, [(r[0], r[1], r[5]) for r in rows_to_insert])
            ins_sql = "INSERT INTO fact_quarantine_audit_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            con.executemany(ins_sql, rows_to_insert)
            con.execute("CHECKPOINT;")
            return len(rows_to_insert)
        finally:
            con.close()

    def get_executive_kpis(self) -> ExecutiveKpiSummary:
        """Query v_executive_reconciliation_kpis or return Honest Zero Baseline ($0.00)."""
        if not self.db_path.is_file():
            return ExecutiveKpiSummary.zero_baseline("duckdb")
        con = self._connect_read()
        try:
            row = con.execute("SELECT * FROM v_executive_reconciliation_kpis").fetchone()
            if row is None:
                return ExecutiveKpiSummary.zero_baseline("duckdb")
            return ExecutiveKpiSummary(
                int(row[0]), float(row[1]), float(row[2]), int(row[3]), int(row[4]),
                int(row[5]), int(row[6]), int(row[7]), float(row[8]), float(row[9]),
                float(row[10]), "duckdb", str(row[11]),
            )
        finally:
            con.close()

    def query_positions_paginated(
        self, status_filter: str = "ALL", search_query: str = "",
        page: int = 1, page_size: int = 25, sort_by: str = "nav_break_abs_desc",
    ) -> dict[str, object]:
        """Execute server-side filtered, searched, and paginated position queries."""
        safe_size, norm_filter = max(1, page_size), status_filter.strip().upper()
        if not self.db_path.is_file():
            return {
                "rows": [], "total_rows": 0, "page": 1, "page_size": safe_size,
                "total_pages": 1, "status_filter": norm_filter, "search_query": search_query,
            }
        base_sql = _ALL_POSITIONS_UNION_SQL
        if norm_filter == "BREAKS_ONLY":
            base_sql = "SELECT * FROM v_nav_and_schema_breaks_triage"
        elif norm_filter == "RECONCILED_ONLY":
            base_sql = (
                f"SELECT * FROM ({_ALL_POSITIONS_UNION_SQL}) "
                "WHERE reconciliation_status IN ('RECONCILED', 'MINOR_TOLERANCE')"
            )
        params: list[object] = []
        where_sql = ""
        if search_query.strip():
            where_sql = (
                " WHERE position_id ILIKE ? OR portfolio_id ILIKE ? OR isin ILIKE ? "
                "OR currency ILIKE ? OR reconciliation_status ILIKE ? OR break_driver ILIKE ?"
            )
            params.extend([f"%{search_query.strip()}%"] * 6)
        order_sql = _SORT_COLUMNS.get(sort_by, _SORT_COLUMNS["nav_break_abs_desc"])
        con = self._connect_read()
        try:
            cnt_q = f"SELECT COUNT(*) FROM ({base_sql}) AS s{where_sql}"
            cnt = con.execute(cnt_q, params).fetchone()
            total_rows = int(cnt[0]) if cnt else 0
            total_pages = max(1, math.ceil(total_rows / safe_size))
            safe_page = min(max(1, page), total_pages)
            page_q = (
                f"SELECT * FROM ({base_sql}) AS s{where_sql} "
                f"ORDER BY {order_sql} LIMIT ? OFFSET ?"
            )
            cur = con.execute(page_q, [*params, safe_size, (safe_page - 1) * safe_size])
            cols = [desc[0] for desc in cur.description]
            rows = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
            return {
                "rows": rows, "total_rows": total_rows, "page": safe_page,
                "page_size": safe_size, "total_pages": total_pages,
                "status_filter": norm_filter, "search_query": search_query,
            }
        finally:
            con.close()

    def execute_readonly_sql(self, sql_query: str) -> dict[str, object]:
        """Validate and execute a read-only SQL query against the DuckDB warehouse."""
        cleaned = sql_query.strip()
        is_ok = bool(cleaned and _ALLOWED_READONLY_RE.match(cleaned))
        if not is_ok or _FORBIDDEN_MUTATION_RE.search(cleaned):
            raise WarehouseAdapterError(
                "[CRITICAL] Mutating or non-readonly SQL statement rejected by Zero-Lock guard",
                code="READONLY_SQL_VIOLATION",
                context={"sql_query": sql_query},
            )
        if not self.db_path.is_file():
            self.initialize_schema()
        t0 = time.perf_counter()
        con = self._connect_read()
        try:
            cur = con.execute(cleaned)
            cols = [desc[0] for desc in cur.description] if cur.description else []
            rows = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
            return {
                "columns": cols, "rows": rows, "row_count": len(rows),
                "execution_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            }
        except Exception as exc:
            raise WarehouseAdapterError(
                f"[CRITICAL] Read-only SQL execution failed: {exc}", context={"sql_query": cleaned}
            ) from exc
        finally:
            con.close()

    def reset_warehouse_to_zero(self) -> None:
        """Truncate all analytical tables back to the Honest Zero Baseline ($0.00)."""
        if not self.db_path.is_file():
            self.initialize_schema()
            return
        con = self._connect_write()
        try:
            con.execute(
                "TRUNCATE TABLE fact_reconciled_positions; "
                "TRUNCATE TABLE fact_quarantine_audit_log; CHECKPOINT;"
            )
        finally:
            con.close()
