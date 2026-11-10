"""Unified Command-Line Interface for Asset-Pipe Institutional Reconciliation.

Orchestrates AssetPipeConfig, S3ParquetAdapter, create_warehouse_adapter,
ReconciliationPipelineService, and SyntheticPortfolioGenerator, emitting
structured JSON telemetry for both operator terminals and the :3000 Console.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.application.ingestion_service import ReconciliationPipelineService
from src.application.synthetic_generator import SyntheticPortfolioGenerator
from src.domain.exceptions import AssetPipeDomainError
from src.infrastructure.config import AssetPipeConfig
from src.infrastructure.s3_parquet_adapter import S3ParquetAdapter
from src.infrastructure.warehouse_factory import create_warehouse_adapter

VALID_ACTIONS: tuple[str, ...] = (
    "status",
    "ingest-valid",
    "inject-corrupt",
    "reconcile",
    "positions",
    "sql",
    "reset",
)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for all 7 operational pipeline actions."""
    parser = argparse.ArgumentParser(
        prog="pipeline_cli",
        description="Asset-Pipe Institutional Financial Reconciliation CLI",
    )
    parser.add_argument("--action", choices=VALID_ACTIONS, default="status")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--breaks-count", type=int, default=15)
    parser.add_argument("--corrupt-count", type=int, default=8)
    parser.add_argument("--seq", type=int, default=1)
    parser.add_argument("--valuation-date", type=str, default="2026-11-10")
    parser.add_argument("--status-filter", type=str, default="ALL")
    parser.add_argument("--search", type=str, default="")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=25)
    parser.add_argument("--sort-by", type=str, default="nav_break_abs_desc")
    parser.add_argument(
        "--query",
        type=str,
        default="SELECT * FROM v_portfolio_aum_rollup LIMIT 25",
    )
    parser.add_argument("--lake-dir", type=str, default="")
    parser.add_argument("--duckdb-path", type=str, default="")
    return parser


def execute_cli_command(
    argv: list[str] | None = None,
    config: AssetPipeConfig | None = None,
) -> dict[str, object]:
    """Parse CLI arguments, execute the requested action, and return a JSON-ready dict."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    lake_override = Path(args.lake_dir) if args.lake_dir else None
    duck_override = Path(args.duckdb_path) if args.duckdb_path else None
    cfg = config or AssetPipeConfig.from_env(
        local_lake_override=lake_override,
        duckdb_override=duck_override,
    )

    lake = S3ParquetAdapter(config=cfg)
    warehouse = create_warehouse_adapter(config=cfg)
    service = ReconciliationPipelineService(lake=lake, warehouse=warehouse)
    generator = SyntheticPortfolioGenerator()

    endpoints = {
        "console_url": f"http://localhost:{cfg.web_port}",
        "minio_console_url": cfg.s3_public_console_url,
        "s3_api_endpoint": cfg.s3_endpoint_url,
        "sql_workbench_url": f"http://localhost:{cfg.sql_ui_port}",
        "warehouse_engine": cfg.warehouse_engine,
    }

    if args.action == "status":
        return {
            "status": "[OK]",
            "action": "status",
            "kpis": warehouse.get_executive_kpis().to_dict(),
            "lake_metrics": lake.get_lake_storage_metrics(),
            "endpoints": endpoints,
        }

    if args.action == "ingest-valid":
        records = generator.generate_clean_institutional_batch(
            count=args.count,
            valuation_date=args.valuation_date,
            batch_seq=args.seq,
        )
        batch_id = f"BATCH-VALID-{args.seq:03d}"
        res = service.ingest_and_reconcile_batch(
            raw_records=records,
            batch_id=batch_id,
            valuation_date=args.valuation_date,
        )
        res["action"] = "ingest-valid"
        res["kpis"] = warehouse.get_executive_kpis().to_dict()
        res["lake_metrics"] = lake.get_lake_storage_metrics()
        return res

    if args.action == "inject-corrupt":
        records = generator.generate_corrupt_and_nav_break_batch(
            valid_with_breaks_count=args.breaks_count,
            schema_corrupt_count=args.corrupt_count,
            valuation_date=args.valuation_date,
            batch_seq=args.seq,
        )
        batch_id = f"BATCH-STRESS-{args.seq:03d}"
        res = service.ingest_and_reconcile_batch(
            raw_records=records,
            batch_id=batch_id,
            valuation_date=args.valuation_date,
        )
        res["action"] = "inject-corrupt"
        res["kpis"] = warehouse.get_executive_kpis().to_dict()
        res["lake_metrics"] = lake.get_lake_storage_metrics()
        return res

    if args.action == "reconcile":
        res = service.run_full_lake_reconciliation(valuation_date=args.valuation_date)
        res["action"] = "reconcile"
        res["kpis"] = warehouse.get_executive_kpis().to_dict()
        res["lake_metrics"] = lake.get_lake_storage_metrics()
        return res

    if args.action == "positions":
        page_data = warehouse.query_positions_paginated(
            status_filter=args.status_filter,
            search_query=args.search,
            page=args.page,
            page_size=args.page_size,
            sort_by=args.sort_by,
        )
        return {"status": "[OK]", "action": "positions", **page_data}

    if args.action == "sql":
        sql_res = warehouse.execute_readonly_sql(args.query)
        return {"status": "[OK]", "action": "sql", **sql_res}

    reset_res = service.reset_entire_platform_to_zero()
    reset_res["action"] = "reset"
    reset_res["endpoints"] = endpoints
    return reset_res


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint printing structured JSON to stdout and returning exit code."""
    try:
        payload = execute_cli_command(argv=argv)
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0
    except AssetPipeDomainError as exc:
        err_payload = {"status": "[CRITICAL]", **exc.to_dict()}
        sys.stdout.write(json.dumps(err_payload, indent=2) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
