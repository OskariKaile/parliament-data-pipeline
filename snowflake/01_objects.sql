-- =====================================================================
-- 01_objects.sql  (run as PIPELINE_ROLE)
-- Schemas and tables.
--   RAW   landing tables. Alteryx empties and reloads them on every run.
--   CORE  merged history. Upserted from RAW by the procedures in 02.
--   AI    Cortex enrichment, the work queue and the evaluation tables.
--   MART  views for the dashboard (05).
--   OPS   config, run log, data-quality results, notifications.
-- Re-runnable: IF NOT EXISTS everywhere, so a re-run keeps the data.
-- =====================================================================
USE ROLE PIPELINE_ROLE;
USE WAREHOUSE PIPELINE_WH;
USE DATABASE PARLIAMENT;

CREATE SCHEMA IF NOT EXISTS RAW  COMMENT = 'Landing tables written by Alteryx (delete + append each run)';
CREATE SCHEMA IF NOT EXISTS CORE COMMENT = 'Deduplicated, merged tables';
CREATE SCHEMA IF NOT EXISTS AI   COMMENT = 'Cortex AI enrichment and evaluation';
CREATE SCHEMA IF NOT EXISTS MART COMMENT = 'Views for the dashboard';
CREATE SCHEMA IF NOT EXISTS OPS  COMMENT = 'Config, run log, data quality, notifications';

-- ---------------------------------------------------------------------
-- RAW: one landing table per Alteryx output. Column names are UPPER_CASE
-- in the Alteryx workflow too, so ODBC field mapping is 1:1.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS RAW.BUSINESS_LANDING (
    BUSINESS_ID        NUMBER(38,0),
    SHORT_NUMBER       VARCHAR(20),
    BUSINESS_TYPE      VARCHAR(20),
    BUSINESS_TYPE_NAME VARCHAR(200),
    TITLE_DE           VARCHAR,
    BODY_DE            VARCHAR,
    REASON_DE          VARCHAR,
    STATUS             VARCHAR(200),
    DEPARTMENT         VARCHAR(20),
    COUNCIL            VARCHAR(10),
    TAG_IDS            VARCHAR(200),
    TAG_NAMES          VARCHAR(1000),
    TAG_COUNT          NUMBER(3,0),
    BODY_CHARS         NUMBER(10,0),
    SUBMITTED_ON       DATE,
    MODIFIED_AT        TIMESTAMP_NTZ,
    SOURCE_URL         VARCHAR(300),
    RUN_ID             VARCHAR(20),
    LOADED_AT          TIMESTAMP_NTZ
) COMMENT = 'Curia Vista business items, one row per item (German), cleaned by Alteryx';

CREATE TABLE IF NOT EXISTS RAW.BUSINESS_TOPIC_LANDING (
    BUSINESS_ID NUMBER(38,0),
    TAG_ID      NUMBER(10,0),
    TOPIC_DE    VARCHAR(200),
    TOPIC_EN    VARCHAR(200),
    RUN_ID      VARCHAR(20)
) COMMENT = 'Official topic tags, exploded to one row per business x topic';

CREATE TABLE IF NOT EXISTS RAW.MEMBER_LANDING (
    MEMBER_ID  NUMBER(38,0),
    FIRST_NAME VARCHAR(200),
    LAST_NAME  VARCHAR(200),
    PARTY      VARCHAR(50),
    PARL_GROUP VARCHAR(50),
    CANTON     VARCHAR(5),
    COUNCIL    VARCHAR(10),
    ACTIVE     BOOLEAN,
    RUN_ID     VARCHAR(20)
) COMMENT = 'Members of the National Council and Council of States in office since the start date';

CREATE TABLE IF NOT EXISTS RAW.BUSINESS_AUTHOR_LANDING (
    BUSINESS_ID  NUMBER(38,0),
    AUTHOR_TYPE  VARCHAR(20),
    MEMBER_ID    NUMBER(38,0),
    COMMITTEE_ID NUMBER(38,0),
    PARTY        VARCHAR(50),
    CANTON       VARCHAR(5),
    RUN_ID       VARCHAR(20)
) COMMENT = 'Who submitted each item (member, committee, canton or parliamentary group)';

-- ---------------------------------------------------------------------
-- CORE
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS CORE.BUSINESS (
    BUSINESS_ID        NUMBER(38,0) NOT NULL PRIMARY KEY,
    SHORT_NUMBER       VARCHAR(20),
    BUSINESS_TYPE      VARCHAR(20),
    BUSINESS_TYPE_NAME VARCHAR(200),
    TITLE_DE           VARCHAR,
    BODY_DE            VARCHAR,
    REASON_DE          VARCHAR,
    STATUS             VARCHAR(200),
    DEPARTMENT         VARCHAR(20),
    COUNCIL            VARCHAR(10),
    TAG_IDS            VARCHAR(200),
    TAG_NAMES          VARCHAR(1000),
    TAG_COUNT          NUMBER(3,0),
    BODY_CHARS         NUMBER(10,0),
    SUBMITTED_ON       DATE,
    MODIFIED_AT        TIMESTAMP_NTZ,
    SOURCE_URL         VARCHAR(300),
    ROW_HASH           VARCHAR(64)  COMMENT 'Hash of every business column; drives the MERGE update',
    CONTENT_HASH       VARCHAR(64)  COMMENT 'Hash of title + texts only; drives Cortex re-enrichment',
    FIRST_LOADED_AT    TIMESTAMP_LTZ,
    LAST_CHANGED_AT    TIMESTAMP_LTZ,
    LAST_RUN_ID        VARCHAR(20)
) CHANGE_TRACKING = TRUE
  COMMENT = 'One row per business item; upserted by CORE.MERGE_BUSINESS()';

CREATE TABLE IF NOT EXISTS CORE.BUSINESS_TOPIC (
    BUSINESS_ID NUMBER(38,0) NOT NULL,
    TAG_ID      NUMBER(10,0) NOT NULL,
    TOPIC_DE    VARCHAR(200),
    TOPIC_EN    VARCHAR(200),
    LAST_RUN_ID VARCHAR(20),
    PRIMARY KEY (BUSINESS_ID, TAG_ID)
);

CREATE TABLE IF NOT EXISTS CORE.MEMBER (
    MEMBER_ID   NUMBER(38,0) NOT NULL PRIMARY KEY,
    FIRST_NAME  VARCHAR(200),
    LAST_NAME   VARCHAR(200),
    PARTY       VARCHAR(50),
    PARL_GROUP  VARCHAR(50),
    CANTON      VARCHAR(5),
    COUNCIL     VARCHAR(10),
    ACTIVE      BOOLEAN,
    LAST_RUN_ID VARCHAR(20),
    UPDATED_AT  TIMESTAMP_LTZ
);

CREATE TABLE IF NOT EXISTS CORE.BUSINESS_AUTHOR (
    BUSINESS_ID  NUMBER(38,0) NOT NULL,
    AUTHOR_TYPE  VARCHAR(20),
    MEMBER_ID    NUMBER(38,0),
    COMMITTEE_ID NUMBER(38,0),
    PARTY        VARCHAR(50),
    CANTON       VARCHAR(5),
    LAST_RUN_ID  VARCHAR(20)
);

-- Reference data: the 28 official topics with an English label and a
-- short description. Source = reference/topics.csv, loaded by Alteryx.
CREATE TABLE IF NOT EXISTS CORE.TOPIC (
    TAG_ID               NUMBER(10,0),
    TOPIC_DE             VARCHAR(200),
    TOPIC_FR             VARCHAR(200),
    TOPIC_EN             VARCHAR(200),
    TOPIC_DESCRIPTION_EN VARCHAR(500)
);

-- Stream = change data capture on CORE.BUSINESS. The Cortex task reads it,
-- so only new or changed items are sent to the (paid) AI functions.
CREATE STREAM IF NOT EXISTS CORE.BUSINESS_CHANGES ON TABLE CORE.BUSINESS
  COMMENT = 'Inserts/updates since the last Cortex enrichment run';

-- ---------------------------------------------------------------------
-- OPS
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS OPS.CONFIG (
    KEY   VARCHAR(100) NOT NULL PRIMARY KEY,
    VALUE VARCHAR(1000)
);

CREATE TABLE IF NOT EXISTS OPS.LOAD_LOG (
    RUN_ID        VARCHAR(20),
    TARGET_TABLE  VARCHAR(100),
    ROWS_LANDED   NUMBER,
    ROWS_INSERTED NUMBER,
    ROWS_UPDATED  NUMBER,
    ROWS_DELETED  NUMBER,
    LOGGED_AT     TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Written by Alteryx: one row per check per run (row-count reconciliation
-- against the API's own $count, rejected rows, orphan keys, unknown topics).
CREATE TABLE IF NOT EXISTS OPS.DQ_RESULTS (
    RUN_ID      VARCHAR(20),
    ENTITY      VARCHAR(50),
    CHECK_NAME  VARCHAR(100),
    EXPECTED    NUMBER,
    ACTUAL      NUMBER,
    FAILED_ROWS NUMBER,
    STATUS      VARCHAR(10),
    DETAIL      VARCHAR(1000),
    INSERTED_AT TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS OPS.DQ_REJECTS (
    RUN_ID      VARCHAR(20),
    ENTITY      VARCHAR(50),
    RECORD_KEY  VARCHAR(100),
    REASON      VARCHAR(200),
    INSERTED_AT TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS OPS.NOTIFICATION_LOG (
    KIND      VARCHAR(30),
    ITEMS     NUMBER,
    RECIPIENT VARCHAR(200),
    SUBJECT   VARCHAR(500),
    SENT_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Who gets the emails. Must be a verified email of a user in this account.
-- SUMMARY_MODEL: the account allows cross-region inference only inside AWS_EU (data stays in
-- the EU); llama3.3-70b is not offered there, llama3.1-70b is (tested 9 Nov 2026).
MERGE INTO OPS.CONFIG c
USING (SELECT 'NOTIFY_EMAIL' AS KEY, 'you@example.com' AS VALUE
       UNION ALL SELECT 'SUMMARY_MODEL', 'llama3.1-70b') s
ON c.KEY = s.KEY
WHEN NOT MATCHED THEN INSERT (KEY, VALUE) VALUES (s.KEY, s.VALUE);
