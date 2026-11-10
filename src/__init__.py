"""Asset-Pipe: Automated Financial & Asset Reconciliation Pipeline.

Root package encapsulating the Clean Architecture layers:
- domain: Pure quantitative financial entities, NAV accounting equations, and GX invariants.
- application: Orchestration use cases and ports for batch/streaming reconciliation.
- infrastructure: Cloud storage (S3/MinIO Parquet) and dual warehouse (DuckDB/Snowflake) adapters.
- interfaces: CLI entrypoints and interactive FinOps/Reconciliation web consoles.
"""
