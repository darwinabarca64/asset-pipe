"""Polymorphic 1-Line Warehouse Adapter Factory (DuckDB Local vs Snowflake Cloud).

Resolves the active WarehousePort implementation from AssetPipeConfig without
coupling domain or application services to concrete database drivers.
"""

from __future__ import annotations

from src.application.ports import WarehousePort
from src.domain.exceptions import WarehouseAdapterError
from src.infrastructure.config import AssetPipeConfig
from src.infrastructure.duckdb_adapter import DuckDBWarehouseAdapter
from src.infrastructure.snowflake_adapter import SnowflakeWarehouseAdapter


def create_warehouse_adapter(config: AssetPipeConfig | None = None) -> WarehousePort:
    """Instantiate and initialize the configured WarehousePort backend."""
    cfg = config or AssetPipeConfig.from_env()
    engine = cfg.warehouse_engine.strip().lower()
    if engine == "duckdb":
        adapter = DuckDBWarehouseAdapter(config=cfg)
        adapter.initialize_schema()
        return adapter
    if engine == "snowflake":
        return SnowflakeWarehouseAdapter(config=cfg)
    raise WarehouseAdapterError(
        f"[CRITICAL] Unsupported WAREHOUSE_ENGINE '{cfg.warehouse_engine}' "
        "(expected 'duckdb' or 'snowflake')",
        code="UNSUPPORTED_WAREHOUSE_ENGINE",
        context={"warehouse_engine": cfg.warehouse_engine},
    )
