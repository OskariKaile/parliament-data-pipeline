-- =====================================================================
-- 00_bootstrap_admin.sql
-- Run ONCE in a Snowsight worksheet as ACCOUNTADMIN (the trial's first user).
-- Creates the warehouse, a cost guard, the PARLIAMENT database, a pipeline
-- role, an email integration and a key-pair service user for Alteryx.
-- Everything after this runs as PIPELINE_ROLE (files 01-06).
-- =====================================================================
USE ROLE ACCOUNTADMIN;

-- Cortex: if a model is not hosted in this region, Snowflake may route the
-- call to another AWS region inside the EU. Data stays in the EU.
ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'AWS_EU';

-- Compute: one extra-small warehouse that suspends after 60 s idle
CREATE WAREHOUSE IF NOT EXISTS PIPELINE_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'Alteryx loads, MERGE procedures, Cortex enrichment, dashboard';

-- Cost guard: suspend the warehouse after 20 credits per month
CREATE OR REPLACE RESOURCE MONITOR PIPELINE_RM
  WITH CREDIT_QUOTA = 20 FREQUENCY = MONTHLY START_TIMESTAMP = IMMEDIATELY
  TRIGGERS ON 80 PERCENT DO NOTIFY
           ON 100 PERCENT DO SUSPEND;
ALTER WAREHOUSE PIPELINE_WH SET RESOURCE_MONITOR = PIPELINE_RM;

-- Role that owns everything the pipeline builds
CREATE ROLE IF NOT EXISTS PIPELINE_ROLE COMMENT = 'Owns the PARLIAMENT database; used by Alteryx and scripts';
GRANT ROLE PIPELINE_ROLE TO ROLE SYSADMIN;

CREATE DATABASE IF NOT EXISTS PARLIAMENT
  COMMENT = 'Swiss Parliament open data: Alteryx ingestion, Snowflake model, Cortex AI';
GRANT OWNERSHIP ON DATABASE PARLIAMENT TO ROLE PIPELINE_ROLE COPY CURRENT GRANTS;
GRANT USAGE, OPERATE, MONITOR ON WAREHOUSE PIPELINE_WH TO ROLE PIPELINE_ROLE;
GRANT EXECUTE TASK, EXECUTE ALERT ON ACCOUNT TO ROLE PIPELINE_ROLE;
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE PIPELINE_ROLE;

-- Email notifications (SYSTEM$SEND_EMAIL). Recipients must be verified
-- email addresses of users in this account (verify yours under Profile).
CREATE NOTIFICATION INTEGRATION IF NOT EXISTS PIPELINE_EMAIL
  TYPE = EMAIL
  ENABLED = TRUE
  COMMENT = 'DQ alerts and the daily watchlist digest';
GRANT USAGE ON INTEGRATION PIPELINE_EMAIL TO ROLE PIPELINE_ROLE;

-- Service user for Alteryx (ODBC) and the helper scripts.
-- TYPE = SERVICE: no password, no MFA prompt; it signs in with an RSA key pair.
-- The private key stays on the laptop (secrets/rsa_key.p8, git-ignored).
CREATE USER IF NOT EXISTS PIPELINE_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = PIPELINE_ROLE
  DEFAULT_WAREHOUSE = PIPELINE_WH
  DEFAULT_NAMESPACE = PARLIAMENT.RAW
  RSA_PUBLIC_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAkTDMUUeONBS4QT97h77nfIRn4En+EY/IRPHSfc3J/T8SiExTGH+C4ZMMTKkwSc9h2fdrxeFCxBczkRjm7YCUIzk2sxrHyC4tWIlmpYdCGX4b9pBh19zZQbfx1aUGJ0XaSqCWWPjK/yFfBxU21OwEC2UWTgZYZJlyA3HUD1d5V6DUlTjyGygI782mWGirRmag5SdK71lbd3VsoRwed3zOJcyKi1bofy1iqhNNwC2WLH4BWgShNKo56RydoLYQ5S6XfhswC+WKgFlFH4Bhuoc/2xR9Sy5ILNIIUIEYPE5ufRDFQd5ci/D0vZn0FcR2bI3gKMLkFFS8fV9UxNlNjL5K5wIDAQAB'
  COMMENT = 'Alteryx + scripts, key-pair auth only';
GRANT ROLE PIPELINE_ROLE TO USER PIPELINE_SVC;

-- The account identifier to put in the ODBC DSN and .env
SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME() AS ACCOUNT_IDENTIFIER,
       CURRENT_USER() AS YOUR_ADMIN_USER;
