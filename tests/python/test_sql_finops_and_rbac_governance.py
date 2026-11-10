"""Pytest Suite for SQL FinOps Guardrails, Zero-Trust RBAC, and Analytical Views.

Verifies the 3 Snowflake FinOps firewalls in sql/00_finops_guardrails.sql,
RSA-2048 passwordless auth, SHA-256 dynamic masking, and RLS in sql/01_security_rbac.sql,
and executes sql/02_analytical_views.sql in DuckDB to validate Honest Zero Baseline
($0.00) and unified NAV + Schema break triage aggregations.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_snowflake_three_finops_guardrails_sql_contract() -> None:
    """Verify sql/00_finops_guardrails.sql enforces all 3 FinOps firewalls and ZSTD stage."""
    sql_path = REPO_ROOT / "sql" / "00_finops_guardrails.sql"
    assert sql_path.is_file()
    content = sql_path.read_text(encoding="utf-8")

    required_clauses = (
        "CREATE OR REPLACE RESOURCE MONITOR ASSET_PIPE_FINOPS_MONITOR",
        "CREDIT_QUOTA = 50",
        "ON 75 PERCENT DO NOTIFY",
        "ON 90 PERCENT DO SUSPEND",
        "ON 100 PERCENT DO SUSPEND_IMMEDIATE",
        "WAREHOUSE_SIZE = 'XSMALL'",
        "AUTO_SUSPEND = 60",
        "AUTO_RESUME = TRUE",
        "INITIALLY_SUSPENDED = TRUE",
        "SCALING_POLICY = 'ECONOMY'",
        "STATEMENT_TIMEOUT_IN_SECONDS = 300",
        "STATEMENT_QUEUED_TIMEOUT_IN_SECONDS = 60",
        "DATA_RETENTION_TIME_IN_DAYS = 7",
        "TYPE = PARQUET COMPRESSION = ZSTD",
    )
    for clause in required_clauses:
        assert clause in content, f"[FINOPS] Missing mandatory FinOps SQL clause: {clause}"


def test_zero_trust_rsa2048_masking_and_rls_sql_contract() -> None:
    """Verify sql/01_security_rbac.sql enforces RSA-2048, SHA-256 masking, and RLS."""
    sql_path = REPO_ROOT / "sql" / "01_security_rbac.sql"
    assert sql_path.is_file()
    content = sql_path.read_text(encoding="utf-8")

    required_security_clauses = (
        "CREATE ROLE IF NOT EXISTS ASSET_PIPE_SVC_ROLE",
        "CREATE ROLE IF NOT EXISTS ASSET_PIPE_AUDITOR_ROLE",
        "CREATE ROLE IF NOT EXISTS ASSET_PIPE_COMPLIANCE_ROLE",
        "PASSWORD = NULL",
        "RSA_PUBLIC_KEY =",
        "RSA_PUBLIC_KEY_2 =",
        "CREATE OR REPLACE MASKING POLICY ASSET_PIPE_DB.RECONCILIATION.PORTFOLIO_ID_MASK",
        "SHA2(val, 256)",
        "CREATE OR REPLACE ROW ACCESS POLICY ASSET_PIPE_DB.RECONCILIATION.JURISDICTION_RLS_POLICY",
        "SET MASKING POLICY",
        "ADD ROW ACCESS POLICY",
    )
    for clause in required_security_clauses:
        assert clause in content, f"[OPSEC] Missing mandatory security SQL clause: {clause}"

    assert "ACCOUNTADMIN" not in content, (
        "[OPSEC] Service user or role must never be granted ACCOUNTADMIN in 01_security_rbac.sql"
    )


def test_analytical_views_honest_zero_baseline_and_aggregations_in_duckdb() -> None:
    """Execute sql/02_analytical_views.sql in DuckDB and verify zero and populated KPIs."""
    sql_path = REPO_ROOT / "sql" / "02_analytical_views.sql"
    assert sql_path.is_file()
    ddl_script = sql_path.read_text(encoding="utf-8")

    con = duckdb.connect(":memory:")
    con.execute(ddl_script)

    zero_rows = con.execute("SELECT * FROM v_executive_reconciliation_kpis").fetchall()
    assert len(zero_rows) == 1
    cols = [desc[0] for desc in con.description]
    zero_kpi = dict(zip(cols, zero_rows[0], strict=True))

    assert zero_kpi["total_positions"] == 0
    assert zero_kpi["validated_ibor_aum_usd"] == 0.0
    assert zero_kpi["custodian_abor_aum_usd"] == 0.0
    assert zero_kpi["reconciled_ok_count"] == 0
    assert zero_kpi["minor_tolerance_count"] == 0
    assert zero_kpi["nav_break_count"] == 0
    assert zero_kpi["schema_rejected_count"] == 0
    assert zero_kpi["total_breaks_and_rejects"] == 0
    assert zero_kpi["gross_nav_variance_usd"] == 0.0
    assert zero_kpi["net_nav_variance_usd"] == 0.0
    assert zero_kpi["max_break_bps"] == 0.0

    con.execute(
        """
        INSERT INTO fact_reconciled_positions VALUES
        (
            'POS-OK-01', 'BLK-ALADDIN-CORE', 'US0378331005', '2026-11-10', 'USD',
            1000.0, 220.0, 1.0, 220000.0, 220000.0, 0.0, 0.0, 0.0,
            'RECONCILED', 'NONE', 0.0, 0.0, 0.0, '{}', 'B-01', '2026-11-10T12:00:00Z'
        ),
        (
            'POS-BRK-02', 'BLK-ALADDIN-CORE', 'US5949181045', '2026-11-10', 'USD',
            1000.0, 105.0, 1.0, 105000.0, 100000.0, 5000.0, 5000.0, 476.1905,
            'NAV_BREAK', 'PRICE_BREAK', 0.0, 5000.0, 0.0, '{}', 'B-01', '2026-11-10T12:00:00Z'
        ),
        (
            'POS-BRK-03', 'BLK-ALADDIN-CORE', 'DE000BAY0017', '2026-11-10', 'EUR',
            1000.0, 95.0, 1.0, 95000.0, 100000.0, -5000.0, 5000.0, 500.0,
            'NAV_BREAK', 'PRICE_BREAK', 0.0, -5000.0, 0.0, '{}', 'B-01', '2026-11-10T12:00:00Z'
        );
        """
    )
    con.execute(
        """
        INSERT INTO fact_quarantine_audit_log VALUES
        (
            'B-01', 'POS-Q-04', 'BLK-ALADDIN-CORE', 'US0378331009', '2026-11-10',
            'GX-03: ISIN_ISO6166_REGEX', 'INVALID_ISIN_ISO6166',
            '[CRITICAL] Check digit mismatch', '{}', '2026-11-10T12:00:00Z'
        );
        """
    )

    pop_row = con.execute("SELECT * FROM v_executive_reconciliation_kpis").fetchone()
    assert pop_row is not None
    pop_kpi = dict(zip(cols, pop_row, strict=True))

    assert pop_kpi["total_positions"] == 3
    assert pop_kpi["validated_ibor_aum_usd"] == 420000.0
    assert pop_kpi["custodian_abor_aum_usd"] == 420000.0
    assert pop_kpi["reconciled_ok_count"] == 1
    assert pop_kpi["nav_break_count"] == 2
    assert pop_kpi["schema_rejected_count"] == 1
    assert pop_kpi["total_breaks_and_rejects"] == 3
    assert pop_kpi["gross_nav_variance_usd"] == 10000.0
    assert pop_kpi["net_nav_variance_usd"] == 0.0
    assert pop_kpi["max_break_bps"] == 500.0

    triage_rows = con.execute(
        "SELECT position_id, reconciliation_status, break_driver "
        "FROM v_nav_and_schema_breaks_triage ORDER BY position_id"
    ).fetchall()
    assert triage_rows == [
        ("POS-BRK-02", "NAV_BREAK", "PRICE_BREAK"),
        ("POS-BRK-03", "NAV_BREAK", "PRICE_BREAK"),
        ("POS-Q-04", "SCHEMA_REJECTED", "SCHEMA_CORRUPTION"),
    ]

    rollup_row = con.execute("SELECT * FROM v_portfolio_aum_rollup").fetchone()
    assert rollup_row is not None
    rollup_cols = [desc[0] for desc in con.description]
    rollup = dict(zip(rollup_cols, rollup_row, strict=True))
    assert rollup["portfolio_id"] == "BLK-ALADDIN-CORE"
    assert rollup["net_fund_break_usd"] == 0.0
    assert rollup["gross_fund_break_usd"] == 10000.0
    assert rollup["nav_break_count"] == 2
    con.close()
