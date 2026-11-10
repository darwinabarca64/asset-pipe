"""Integration Tests for Dual Warehouse Adapters (DuckDB / Snowflake) and CLI.

Validates Honest Zero Baseline ($0.00), Zero-Hold Write Lock DuckDB persistence,
Arrow Zero-Copy upserts, BREAKS_ONLY server-side triage pagination, read-only SQL
guardrails, RSA-2048 PKCS#8 DER key loading, and full platform reset to zero.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from src.application.ports import WarehousePort
from src.domain.exceptions import WarehouseAdapterError
from src.infrastructure.config import AssetPipeConfig
from src.infrastructure.duckdb_adapter import DuckDBWarehouseAdapter
from src.infrastructure.snowflake_adapter import (
    SnowflakeWarehouseAdapter,
    load_rsa_private_key_der,
)
from src.infrastructure.warehouse_factory import create_warehouse_adapter
from src.interfaces.cli.pipeline_cli import execute_cli_command, main


def _make_test_config(tmp_path: Path, engine: str = "duckdb") -> AssetPipeConfig:
    """Create an isolated AssetPipeConfig pointing to pytest tmp_path."""
    base = AssetPipeConfig.from_env(
        local_lake_override=tmp_path / "s3_lake",
        duckdb_override=tmp_path / "test_asset_pipe.duckdb",
    )
    return replace(
        base,
        s3_endpoint_url="http://127.0.0.1:59999",
        warehouse_engine=engine,
    )


def test_duckdb_adapter_honest_zero_baseline_and_protocol(tmp_path: Path) -> None:
    """Verify freshly initialized DuckDBWarehouseAdapter reports $0.00 across all KPIs."""
    cfg = _make_test_config(tmp_path, engine="duckdb")
    adapter = create_warehouse_adapter(cfg)
    assert isinstance(adapter, WarehousePort)
    assert isinstance(adapter, DuckDBWarehouseAdapter)

    kpis = adapter.get_executive_kpis()
    assert kpis.total_positions == 0
    assert kpis.validated_ibor_aum_usd == 0.0
    assert kpis.custodian_abor_aum_usd == 0.0
    assert kpis.reconciled_ok_count == 0
    assert kpis.nav_break_count == 0
    assert kpis.schema_rejected_count == 0
    assert kpis.total_breaks_and_rejects == 0
    assert kpis.gross_nav_variance_usd == 0.0
    assert kpis.warehouse_engine == "duckdb"


def test_cli_ingest_corrupt_pagination_sql_and_reset_to_zero(tmp_path: Path) -> None:
    """Execute full lifecycle via CLI: status -> ingest-valid -> inject-corrupt -> reset."""
    cfg = _make_test_config(tmp_path, engine="duckdb")

    status_zero = execute_cli_command(["--action", "status"], config=cfg)
    assert status_zero["status"] == "[OK]"
    assert status_zero["kpis"]["total_positions"] == 0
    assert status_zero["kpis"]["validated_ibor_aum_usd"] == 0.0

    valid_res = execute_cli_command(
        ["--action", "ingest-valid", "--count", "20", "--seq", "1"],
        config=cfg,
    )
    assert valid_res["status"] == "[OK]"
    assert valid_res["promoted_count"] == 20
    assert valid_res["quarantined_count"] == 0
    assert valid_res["kpis"]["total_positions"] == 20
    assert valid_res["kpis"]["validated_ibor_aum_usd"] > 0.0
    assert valid_res["kpis"]["nav_break_count"] == 0

    stress_res = execute_cli_command(
        ["--action", "inject-corrupt", "--breaks-count", "9", "--corrupt-count", "6", "--seq", "2"],
        config=cfg,
    )
    assert stress_res["status"] == "[OK]"
    assert stress_res["promoted_count"] == 9
    assert stress_res["quarantined_count"] == 6
    assert stress_res["kpis"]["total_positions"] == 29
    assert stress_res["kpis"]["nav_break_count"] == 9
    assert stress_res["kpis"]["schema_rejected_count"] == 6
    assert stress_res["kpis"]["total_breaks_and_rejects"] == 15

    breaks_page = execute_cli_command(
        [
            "--action", "positions", "--status-filter", "BREAKS_ONLY",
            "--page", "1", "--page-size", "50",
        ],
        config=cfg,
    )
    assert breaks_page["total_rows"] == 15
    statuses = {row["reconciliation_status"] for row in breaks_page["rows"]}
    assert statuses == {"NAV_BREAK", "SCHEMA_REJECTED"}

    reconciled_page = execute_cli_command(
        ["--action", "positions", "--status-filter", "RECONCILED_ONLY"],
        config=cfg,
    )
    assert reconciled_page["total_rows"] == 20
    for row in reconciled_page["rows"]:
        assert row["reconciliation_status"] in {"RECONCILED", "MINOR_TOLERANCE"}

    searched = execute_cli_command(
        ["--action", "positions", "--status-filter", "ALL", "--search", "BLK-ALADDIN-CORE"],
        config=cfg,
    )
    assert searched["total_rows"] > 0
    for row in searched["rows"]:
        assert "BLK-ALADDIN-CORE" in str(row["portfolio_id"])

    reconcile_res = execute_cli_command(["--action", "reconcile"], config=cfg)
    assert reconcile_res["status"] == "[OK]"
    assert reconcile_res["total_reconciled_positions"] == 29

    reset_res = execute_cli_command(["--action", "reset"], config=cfg)
    assert reset_res["status"] == "[OK]"
    assert reset_res["kpis"]["total_positions"] == 0
    assert reset_res["kpis"]["validated_ibor_aum_usd"] == 0.0
    assert reset_res["kpis"]["total_breaks_and_rejects"] == 0
    assert reset_res["lake_metrics"]["total_objects"] == 0
    assert reset_res["lake_metrics"]["total_bytes"] == 0


def test_readonly_sql_workbench_guard_allows_select_and_blocks_mutations(
    tmp_path: Path,
) -> None:
    """Verify execute_readonly_sql permits SELECT rollups and blocks DROP/DELETE."""
    cfg = _make_test_config(tmp_path, engine="duckdb")
    execute_cli_command(["--action", "ingest-valid", "--count", "10"], config=cfg)
    warehouse = DuckDBWarehouseAdapter(config=cfg)

    sql_ok = warehouse.execute_readonly_sql("SELECT * FROM v_portfolio_aum_rollup")
    assert "portfolio_id" in sql_ok["columns"]
    assert int(sql_ok["row_count"]) > 0

    for forbidden_sql in (
        "DROP TABLE fact_reconciled_positions",
        "DELETE FROM fact_reconciled_positions",
        "SELECT * FROM fact_reconciled_positions; DROP TABLE fact_quarantine_audit_log",
    ):
        with pytest.raises(WarehouseAdapterError) as exc_info:
            warehouse.execute_readonly_sql(forbidden_sql)
        assert exc_info.value.code == "READONLY_SQL_VIOLATION"

    exit_code = main(
        [
            "--action", "sql",
            "--query", "DROP TABLE fact_reconciled_positions",
            "--lake-dir", str(tmp_path / "s3_lake"),
            "--duckdb-path", str(tmp_path / "test_asset_pipe.duckdb"),
        ]
    )
    assert exit_code == 1


def test_snowflake_adapter_rsa2048_key_loader_and_finops_session_params(
    tmp_path: Path,
) -> None:
    """Verify RSA-2048 PKCS#8 DER key generation/loading and Snowflake FinOps params."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    key_file = tmp_path / "test_rsa_2048.p8"
    key_file.write_bytes(pem_bytes)

    der_bytes = load_rsa_private_key_der(key_file)
    assert isinstance(der_bytes, bytes)
    assert len(der_bytes) > 1000

    cfg = replace(
        _make_test_config(tmp_path, engine="snowflake"),
        snowflake_private_key_path=str(key_file),
    )
    snow_adapter = create_warehouse_adapter(cfg)
    assert isinstance(snow_adapter, SnowflakeWarehouseAdapter)

    params = snow_adapter.get_connection_parameters()
    assert params["private_key"] == der_bytes
    assert params["session_parameters"] == {
        "STATEMENT_TIMEOUT_IN_SECONDS": 300,
        "STATEMENT_QUEUED_TIMEOUT_IN_SECONDS": 60,
    }
    cold_kpis = snow_adapter.get_executive_kpis()
    assert cold_kpis.total_positions == 0
    assert cold_kpis.validated_ibor_aum_usd == 0.0
    assert cold_kpis.warehouse_engine == "snowflake"

    with pytest.raises(WarehouseAdapterError):
        create_warehouse_adapter(replace(cfg, warehouse_engine="invalid_engine"))
