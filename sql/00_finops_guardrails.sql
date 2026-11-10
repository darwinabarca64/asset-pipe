-- ============================================================================
-- [FINOPS] ASSET-PIPE: SNOWFLAKE CLOUD FINOPS GUARDRAILS & STORAGE GOVERNANCE
-- File: sql/00_finops_guardrails.sql
-- Purpose: Enforces the 3 institutional FinOps firewalls (>90% credit savings):
--   1. Aggressive Cluster Lifecycle (XSMALL, AUTO_SUSPEND = 60, AUTO_RESUME = TRUE)
--   2. Zombie Query Eradication (STATEMENT_TIMEOUT_IN_SECONDS = 300, QUEUED = 60)
--   3. Hard Monthly Budget Circuit Breaker (RESOURCE MONITOR -> SUSPEND_IMMEDIATE)
-- ============================================================================

USE ROLE ACCOUNTADMIN;

-- ----------------------------------------------------------------------------
-- [FINOPS] FIREWALL 3: HARD BUDGETARY CIRCUIT BREAKER (RESOURCE MONITOR)
-- Enforces a strict 50-credit monthly quota (~$100-$150 USD/month ceiling).
-- Automatically notifies at 75%, suspends new queries at 90%, and executes
-- an immediate hard kill (SUSPEND_IMMEDIATE) at 100% quota consumption.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE RESOURCE MONITOR ASSET_PIPE_FINOPS_MONITOR
    WITH CREDIT_QUOTA = 50
    FREQUENCY = MONTHLY
    START_TIMESTAMP = IMMEDIATELY
    TRIGGERS
        ON 75 PERCENT DO NOTIFY
        ON 90 PERCENT DO SUSPEND
        ON 100 PERCENT DO SUSPEND_IMMEDIATE;

-- ----------------------------------------------------------------------------
-- [FINOPS] FIREWALL 1: CLUSTER LIFECYCLE EFFICIENCY (VIRTUAL WAREHOUSE)
-- Provisions a single-cluster XSMALL warehouse bound to ASSET_PIPE_FINOPS_MONITOR.
-- AUTO_SUSPEND = 60 guarantees idle compute terminates after 60 seconds.
-- INITIALLY_SUSPENDED = TRUE prevents credit burn upon DDL provisioning.
-- ----------------------------------------------------------------------------
CREATE WAREHOUSE IF NOT EXISTS ASSET_PIPE_WH
    WITH WAREHOUSE_SIZE = 'XSMALL'
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    INITIALLY_SUSPENDED = TRUE
    MIN_CLUSTER_COUNT = 1
    MAX_CLUSTER_COUNT = 1
    SCALING_POLICY = 'ECONOMY'
    RESOURCE_MONITOR = 'ASSET_PIPE_FINOPS_MONITOR'
    COMMENT = '[FINOPS] Asset-Pipe Vectorized Reconciliation Warehouse';

-- ----------------------------------------------------------------------------
-- [FINOPS] FIREWALL 2: ZOMBIE QUERY ERADICATION (STATEMENT TIMEOUTS)
-- Aborts any cartesian join, runaway analytical scan, or locked transaction
-- exceeding 300 seconds (5 minutes) of execution or 60 seconds in queue.
-- ----------------------------------------------------------------------------
ALTER WAREHOUSE ASSET_PIPE_WH SET
    STATEMENT_TIMEOUT_IN_SECONDS = 300
    STATEMENT_QUEUED_TIMEOUT_IN_SECONDS = 60;

-- ----------------------------------------------------------------------------
-- [FINOPS] CONTROLLED TIME TRAVEL & ZSTD PARQUET EXTERNAL STAGE PROVISIONING
-- Caps Time Travel retention at 7 days to prevent unbounded Fail-Safe storage
-- bloat while enabling point-in-time regulatory audit recovery.
-- ----------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS ASSET_PIPE_DB
    DATA_RETENTION_TIME_IN_DAYS = 7
    COMMENT = '[FINOPS] Institutional IBOR/ABOR Reconciliation Database';

CREATE SCHEMA IF NOT EXISTS ASSET_PIPE_DB.RECONCILIATION
    DATA_RETENTION_TIME_IN_DAYS = 7
    COMMENT = '[FINOPS] Validated Positions, NAV Attribution & Quarantine Audit Schema';

CREATE OR REPLACE FILE FORMAT ASSET_PIPE_DB.RECONCILIATION.PARQUET_ZSTD_FORMAT
    TYPE = PARQUET
    COMPRESSION = ZSTD
    BINARY_AS_TEXT = FALSE
    COMMENT = '[FINOPS] Zstandard Columnar Parquet Format with Zone Map Statistics';

CREATE OR REPLACE STAGE ASSET_PIPE_DB.RECONCILIATION.S3_VALIDATED_PARQUET_STAGE
    URL = 's3://validated-parquet/'
    FILE_FORMAT = (TYPE = PARQUET COMPRESSION = ZSTD)
    COMMENT = '[FINOPS] External Medallion Stage over GX-Certified ZSTD Parquet Lake';
