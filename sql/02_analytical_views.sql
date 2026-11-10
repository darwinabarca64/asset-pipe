-- ============================================================================
-- ASSET-PIPE: DUAL WAREHOUSE ANALYTICAL TABLES & EXECUTIVE VIEWS (ANSI SQL)
-- File: sql/02_analytical_views.sql
-- Compatibility: 100% ANSI SQL compatible with DuckDB Local & Snowflake Cloud
-- Purpose: Defines fact tables for reconciled positions and quarantine logs,
--          plus Honest Zero Baseline ($0.00) KPI views and unified break triage.
-- ============================================================================

CREATE TABLE IF NOT EXISTS fact_reconciled_positions (
    position_id VARCHAR NOT NULL,
    portfolio_id VARCHAR NOT NULL,
    isin VARCHAR(12) NOT NULL,
    valuation_date VARCHAR(10) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    units DOUBLE NOT NULL,
    price DOUBLE NOT NULL,
    fx_rate DOUBLE NOT NULL,
    calculated_nav DOUBLE NOT NULL,
    custodian_reported_nav DOUBLE NOT NULL,
    nav_break_signed DOUBLE NOT NULL,
    nav_break_abs DOUBLE NOT NULL,
    nav_break_bps DOUBLE NOT NULL,
    reconciliation_status VARCHAR(32) NOT NULL,
    break_driver VARCHAR(32) NOT NULL,
    qty_effect_usd DOUBLE NOT NULL,
    price_effect_usd DOUBLE NOT NULL,
    fx_effect_usd DOUBLE NOT NULL,
    extra_metadata_json VARCHAR NOT NULL,
    batch_id VARCHAR NOT NULL,
    updated_at_utc VARCHAR NOT NULL,
    PRIMARY KEY (portfolio_id, isin, valuation_date)
);

CREATE TABLE IF NOT EXISTS fact_quarantine_audit_log (
    batch_id VARCHAR NOT NULL,
    position_id VARCHAR NOT NULL,
    portfolio_id VARCHAR NOT NULL,
    isin VARCHAR NOT NULL,
    valuation_date VARCHAR NOT NULL,
    rule_id VARCHAR NOT NULL,
    violation_code VARCHAR NOT NULL,
    diagnostic_message VARCHAR NOT NULL,
    raw_payload_json VARCHAR NOT NULL,
    quarantined_at_utc VARCHAR NOT NULL
);

-- ----------------------------------------------------------------------------
-- VIEW 1: v_executive_reconciliation_kpis
-- Mathematical Honest Zero Baseline ($0.00 and 0 counts when tables are empty).
-- Feeds the 5 Executive KPI Cards on the :3000 Institutional Console.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_executive_reconciliation_kpis AS
WITH pos_stats AS (
    SELECT
        CAST(COUNT(*) AS BIGINT) AS total_positions,
        ROUND(COALESCE(SUM(calculated_nav), 0.0), 4) AS validated_ibor_aum_usd,
        ROUND(COALESCE(SUM(custodian_reported_nav), 0.0), 4) AS custodian_abor_aum_usd,
        CAST(
            COALESCE(SUM(CASE WHEN reconciliation_status = 'RECONCILED' THEN 1 ELSE 0 END), 0)
            AS BIGINT
        ) AS reconciled_ok_count,
        CAST(
            COALESCE(SUM(CASE WHEN reconciliation_status = 'MINOR_TOLERANCE' THEN 1 ELSE 0 END), 0)
            AS BIGINT
        ) AS minor_tolerance_count,
        CAST(
            COALESCE(SUM(CASE WHEN reconciliation_status = 'NAV_BREAK' THEN 1 ELSE 0 END), 0)
            AS BIGINT
        ) AS nav_break_count,
        ROUND(COALESCE(SUM(nav_break_abs), 0.0), 4) AS gross_nav_variance_usd,
        ROUND(COALESCE(SUM(nav_break_signed), 0.0), 4) AS net_nav_variance_usd,
        ROUND(COALESCE(MAX(nav_break_bps), 0.0), 4) AS max_break_bps,
        COALESCE(MAX(updated_at_utc), '1970-01-01T00:00:00Z') AS last_updated_utc
    FROM fact_reconciled_positions
),
quar_stats AS (
    SELECT
        CAST(COUNT(*) AS BIGINT) AS schema_rejected_count
    FROM fact_quarantine_audit_log
)
SELECT
    p.total_positions,
    p.validated_ibor_aum_usd,
    p.custodian_abor_aum_usd,
    p.reconciled_ok_count,
    p.minor_tolerance_count,
    p.nav_break_count,
    q.schema_rejected_count,
    CAST(p.nav_break_count + q.schema_rejected_count AS BIGINT) AS total_breaks_and_rejects,
    p.gross_nav_variance_usd,
    p.net_nav_variance_usd,
    p.max_break_bps,
    p.last_updated_utc
FROM pos_stats p
CROSS JOIN quar_stats q;

-- ----------------------------------------------------------------------------
-- VIEW 2: v_nav_and_schema_breaks_triage
-- Unified forensic view combining accounting NAV_BREAK rows with quarantined
-- SCHEMA_REJECTED rows for Card 4 (NAV & Schema Breaks) drill-down triage.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_nav_and_schema_breaks_triage AS
SELECT
    batch_id,
    position_id,
    portfolio_id,
    isin,
    valuation_date,
    currency,
    units,
    price,
    fx_rate,
    calculated_nav,
    custodian_reported_nav,
    nav_break_signed,
    nav_break_abs,
    nav_break_bps,
    reconciliation_status,
    break_driver,
    qty_effect_usd,
    price_effect_usd,
    fx_effect_usd,
    'GX-07: NAV_EQUATION_MATCH' AS rule_id,
    '[NAV-BREAK] Material valuation variance detected' AS diagnostic_message,
    updated_at_utc AS event_timestamp_utc
FROM fact_reconciled_positions
WHERE reconciliation_status = 'NAV_BREAK'
UNION ALL
SELECT
    batch_id,
    position_id,
    portfolio_id,
    isin,
    valuation_date,
    'N/A' AS currency,
    0.0 AS units,
    0.0 AS price,
    0.0 AS fx_rate,
    0.0 AS calculated_nav,
    0.0 AS custodian_reported_nav,
    0.0 AS nav_break_signed,
    0.0 AS nav_break_abs,
    0.0 AS nav_break_bps,
    'SCHEMA_REJECTED' AS reconciliation_status,
    'SCHEMA_CORRUPTION' AS break_driver,
    0.0 AS qty_effect_usd,
    0.0 AS price_effect_usd,
    0.0 AS fx_effect_usd,
    rule_id,
    diagnostic_message,
    quarantined_at_utc AS event_timestamp_utc
FROM fact_quarantine_audit_log;

-- ----------------------------------------------------------------------------
-- VIEW 3: v_portfolio_aum_rollup
-- Aggregates IBOR AUM, Custodian ABOR AUM, Net Fund Break, and Gross Fund Break
-- per (portfolio_id, valuation_date) to prevent compensating error masking.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_portfolio_aum_rollup AS
SELECT
    portfolio_id,
    valuation_date,
    CAST(COUNT(*) AS BIGINT) AS total_positions,
    ROUND(COALESCE(SUM(calculated_nav), 0.0), 4) AS ibor_aum_usd,
    ROUND(COALESCE(SUM(custodian_reported_nav), 0.0), 4) AS custodian_aum_usd,
    ROUND(COALESCE(SUM(nav_break_signed), 0.0), 4) AS net_fund_break_usd,
    ROUND(COALESCE(SUM(nav_break_abs), 0.0), 4) AS gross_fund_break_usd,
    CAST(
        COALESCE(SUM(CASE WHEN reconciliation_status = 'NAV_BREAK' THEN 1 ELSE 0 END), 0)
        AS BIGINT
    ) AS nav_break_count
FROM fact_reconciled_positions
GROUP BY portfolio_id, valuation_date
ORDER BY portfolio_id, valuation_date;
