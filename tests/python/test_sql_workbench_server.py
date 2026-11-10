"""Tests for the Zero-Lock Read-Only Warehouse SQL Workbench Server (:4213).

Validates HTML5 rendering, Zero-Emoji compliance, presence of the 5 one-click
institutional SQL templates, Honest Zero Baseline ($0.00) query execution, and
HTTP 400 [CRITICAL] rejection of mutating SQL statements.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from src.infrastructure.config import AssetPipeConfig
from src.interfaces.cli.pipeline_cli import execute_cli_command
from src.interfaces.web.sql_workbench_server import (
    PRECONFIGURED_INSTITUTIONAL_QUERIES,
    execute_workbench_query,
    render_sql_workbench_html,
)

EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _make_workbench_config(tmp_path: Path) -> AssetPipeConfig:
    """Build an isolated AssetPipeConfig backed by pytest tmp_path."""
    base = AssetPipeConfig.from_env(
        local_lake_override=tmp_path / "s3_lake",
        duckdb_override=tmp_path / "workbench_test.duckdb",
    )
    return replace(base, s3_endpoint_url="http://127.0.0.1:59999")


def test_render_sql_workbench_html_contains_five_presets_and_zero_emojis(
    tmp_path: Path,
) -> None:
    """Verify :4213 HTML includes 5 presets, :3000 link, and zero Unicode emojis."""
    cfg = _make_workbench_config(tmp_path)
    html = render_sql_workbench_html(cfg)

    assert html.startswith("<!DOCTYPE html>")
    assert EMOJI_RE.search(html) is None
    assert "[OK] READ-ONLY SNAPSHOT ISOLATION" in html
    assert "Back to Executive Console (:3000)" in html
    assert "http://localhost:3000" in html
    assert "Run Read-Only Query (Ctrl+Enter)" in html

    assert len(PRECONFIGURED_INSTITUTIONAL_QUERIES) == 5
    for label, sql in PRECONFIGURED_INSTITUTIONAL_QUERIES:
        assert label in html
        assert sql in html


def test_execute_workbench_query_honest_zero_baseline_and_five_templates(
    tmp_path: Path,
) -> None:
    """Verify 200 OK Honest Zero Baseline ($0.00) and execution of all 5 templates."""
    cfg = _make_workbench_config(tmp_path)

    code, payload = execute_workbench_query(
        "SELECT * FROM v_executive_reconciliation_kpis;", cfg
    )
    assert code == 200
    assert payload["status"] == "[OK]"
    rows = payload["rows"]
    assert isinstance(rows, list) and len(rows) == 1
    first = rows[0]
    assert first["total_positions"] == 0
    assert first["validated_ibor_aum_usd"] == 0.0
    assert first["custodian_abor_aum_usd"] == 0.0
    assert first["total_breaks_and_rejects"] == 0

    execute_cli_command(
        ["--action", "inject-corrupt", "--breaks-count", "6", "--corrupt-count", "4"],
        config=cfg,
    )

    for _label, preset_sql in PRECONFIGURED_INSTITUTIONAL_QUERIES:
        status_code, res = execute_workbench_query(preset_sql, cfg)
        assert status_code == 200
        assert res["status"] == "[OK]"
        assert int(res["row_count"]) > 0


def test_execute_workbench_query_blocks_destructive_ddl_and_dml_with_400(
    tmp_path: Path,
) -> None:
    """Verify mutating SQL statements return HTTP 400 with [CRITICAL] status."""
    cfg = _make_workbench_config(tmp_path)

    destructive_queries = (
        "DROP TABLE fact_reconciled_positions",
        "DELETE FROM fact_reconciled_positions",
        "TRUNCATE TABLE fact_quarantine_audit_log",
        "UPDATE fact_reconciled_positions SET price = 0.0",
        "INSERT INTO fact_reconciled_positions VALUES ('1')",
    )
    for bad_sql in destructive_queries:
        code, payload = execute_workbench_query(bad_sql, cfg)
        assert code == 400
        assert payload["status"] == "[CRITICAL]"
        assert payload["code"] == "READONLY_SQL_VIOLATION"
