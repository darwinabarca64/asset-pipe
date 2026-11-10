-- ============================================================================
-- [OPSEC] ASSET-PIPE: ZERO-TRUST RBAC, RSA-2048 KEY-PAIR AUTH, MASKING & RLS
-- File: sql/01_security_rbac.sql
-- Purpose: Implements banking-grade Zero-Trust security controls:
--   1. Least-Privilege Role Hierarchy (SVC_ROLE, AUDITOR_ROLE, COMPLIANCE_ROLE)
--   2. Passwordless Cryptographic Authentication (RSA-2048 Dual-Slot Rotation)
--   3. Cardinality-Preserving Dynamic Data Masking (SHA2-256 Deterministic Hash)
--   4. Multi-Jurisdiction Row-Level Security (ROW ACCESS POLICY by ISIN Prefix)
-- ============================================================================

USE ROLE SECURITYADMIN;

-- ----------------------------------------------------------------------------
-- 1. LEAST-PRIVILEGE RBAC ROLE HIERARCHY
-- Service accounts and analytical roles are strictly isolated from admin roles.
-- ----------------------------------------------------------------------------
CREATE ROLE IF NOT EXISTS ASSET_PIPE_SVC_ROLE
    COMMENT = '[OPSEC] Automated pipeline ingestion and reconciliation service role';

CREATE ROLE IF NOT EXISTS ASSET_PIPE_AUDITOR_ROLE
    COMMENT = '[OPSEC] Read-only external/internal auditor role with masked fund IDs';

CREATE ROLE IF NOT EXISTS ASSET_PIPE_COMPLIANCE_ROLE
    COMMENT = '[OPSEC] Regulatory compliance officer role with unmasked PII/Fund visibility';

GRANT ROLE ASSET_PIPE_SVC_ROLE TO ROLE SYSADMIN;
GRANT ROLE ASSET_PIPE_AUDITOR_ROLE TO ROLE ASSET_PIPE_COMPLIANCE_ROLE;
GRANT ROLE ASSET_PIPE_COMPLIANCE_ROLE TO ROLE SYSADMIN;

-- ----------------------------------------------------------------------------
-- 2. PASSWORDLESS RSA-2048 KEY-PAIR SERVICE USER (ZERO-DOWNTIME DUAL SLOT)
-- Enforces PASSWORD = NULL so password brute-force attacks are impossible.
-- Supports RSA_PUBLIC_KEY and RSA_PUBLIC_KEY_2 for seamless key rotation.
-- ----------------------------------------------------------------------------
CREATE USER IF NOT EXISTS ASSET_PIPE_SVC_USER
    PASSWORD = NULL
    DEFAULT_ROLE = ASSET_PIPE_SVC_ROLE
    DEFAULT_WAREHOUSE = ASSET_PIPE_WH
    MUST_CHANGE_PASSWORD = FALSE
    RSA_PUBLIC_KEY = '<RSA_2048_PUBLIC_KEY_PKCS8_SLOT_1>'
    RSA_PUBLIC_KEY_2 = '<RSA_2048_PUBLIC_KEY_PKCS8_SLOT_2_FOR_ZERO_DOWNTIME_ROTATION>'
    COMMENT = '[OPSEC] Passwordless RSA-2048 cryptographic service identity';

GRANT ROLE ASSET_PIPE_SVC_ROLE TO USER ASSET_PIPE_SVC_USER;

-- ----------------------------------------------------------------------------
-- 3. STRICT WAREHOUSE, DATABASE, SCHEMA, AND TABLE PRIVILEGE GRANTS
-- ----------------------------------------------------------------------------
GRANT USAGE, OPERATE ON WAREHOUSE ASSET_PIPE_WH TO ROLE ASSET_PIPE_SVC_ROLE;
GRANT USAGE ON WAREHOUSE ASSET_PIPE_WH TO ROLE ASSET_PIPE_AUDITOR_ROLE;
GRANT USAGE ON WAREHOUSE ASSET_PIPE_WH TO ROLE ASSET_PIPE_COMPLIANCE_ROLE;

GRANT USAGE ON DATABASE ASSET_PIPE_DB TO ROLE ASSET_PIPE_SVC_ROLE;
GRANT USAGE ON DATABASE ASSET_PIPE_DB TO ROLE ASSET_PIPE_AUDITOR_ROLE;
GRANT USAGE ON DATABASE ASSET_PIPE_DB TO ROLE ASSET_PIPE_COMPLIANCE_ROLE;

GRANT USAGE ON SCHEMA ASSET_PIPE_DB.RECONCILIATION TO ROLE ASSET_PIPE_SVC_ROLE;
GRANT USAGE ON SCHEMA ASSET_PIPE_DB.RECONCILIATION TO ROLE ASSET_PIPE_AUDITOR_ROLE;
GRANT USAGE ON SCHEMA ASSET_PIPE_DB.RECONCILIATION TO ROLE ASSET_PIPE_COMPLIANCE_ROLE;

GRANT SELECT, INSERT, UPDATE, TRUNCATE
    ON ALL TABLES IN SCHEMA ASSET_PIPE_DB.RECONCILIATION
    TO ROLE ASSET_PIPE_SVC_ROLE;

GRANT SELECT
    ON ALL VIEWS IN SCHEMA ASSET_PIPE_DB.RECONCILIATION
    TO ROLE ASSET_PIPE_AUDITOR_ROLE;

GRANT SELECT
    ON ALL TABLES IN SCHEMA ASSET_PIPE_DB.RECONCILIATION
    TO ROLE ASSET_PIPE_COMPLIANCE_ROLE;

-- ----------------------------------------------------------------------------
-- 4. CARDINALITY-PRESERVING DYNAMIC DATA MASKING (SHA-256)
-- Masks portfolio_id for non-privileged roles while preserving GROUP BY / JOIN
-- cardinality via deterministic SHA2(val, 256) prefixing.
-- ----------------------------------------------------------------------------
USE ROLE SYSADMIN;

CREATE OR REPLACE MASKING POLICY ASSET_PIPE_DB.RECONCILIATION.PORTFOLIO_ID_MASK
    AS (val STRING) RETURNS STRING ->
    CASE
        WHEN CURRENT_ROLE() IN ('ASSET_PIPE_SVC_ROLE', 'ASSET_PIPE_COMPLIANCE_ROLE')
            THEN val
        ELSE CONCAT('MASKED-FUND-', SUBSTR(SHA2(val, 256), 1, 8))
    END;

-- ----------------------------------------------------------------------------
-- 5. MULTI-JURISDICTION ROW-LEVEL SECURITY (ROW ACCESS POLICY)
-- Restricts position visibility by ISO 6166 2-letter country prefix when queried
-- by regional audit roles.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ASSET_PIPE_DB.RECONCILIATION.jurisdiction_entitlement_map (
    role_name VARCHAR(64) NOT NULL,
    allowed_prefix VARCHAR(2) NOT NULL,
    PRIMARY KEY (role_name, allowed_prefix)
);

CREATE OR REPLACE ROW ACCESS POLICY ASSET_PIPE_DB.RECONCILIATION.JURISDICTION_RLS_POLICY
    AS (isin_code STRING) RETURNS BOOLEAN ->
    CURRENT_ROLE() IN ('ASSET_PIPE_SVC_ROLE', 'ASSET_PIPE_COMPLIANCE_ROLE')
    OR EXISTS (
        SELECT 1
        FROM ASSET_PIPE_DB.RECONCILIATION.jurisdiction_entitlement_map m
        WHERE m.role_name = CURRENT_ROLE()
          AND m.allowed_prefix = SUBSTR(isin_code, 1, 2)
    );

ALTER TABLE ASSET_PIPE_DB.RECONCILIATION.fact_reconciled_positions
    MODIFY COLUMN portfolio_id
    SET MASKING POLICY ASSET_PIPE_DB.RECONCILIATION.PORTFOLIO_ID_MASK;

ALTER TABLE ASSET_PIPE_DB.RECONCILIATION.fact_reconciled_positions
    ADD ROW ACCESS POLICY ASSET_PIPE_DB.RECONCILIATION.JURISDICTION_RLS_POLICY
    ON (isin);
