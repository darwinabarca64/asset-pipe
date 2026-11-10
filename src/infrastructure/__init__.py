"""Infrastructure Layer: Storage and Dual-Engine Warehouse Adapters.

Implements concrete adapters for AWS S3 / MinIO Parquet persistence and
dual-engine analytical execution across DuckDB (local zero-cost) and
Snowflake (enterprise cloud with FinOps governance).
"""
