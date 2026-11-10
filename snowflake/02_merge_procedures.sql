-- =====================================================================
-- 02_merge_procedures.sql  (run as PIPELINE_ROLE)
-- Upsert procedures RAW -> CORE. Alteryx calls each one as the
-- "Post Create SQL Statement" of the matching Output Data tool, so the
-- merge runs right after that landing table is written:
--     CALL CORE.MERGE_BUSINESS();      (Output: RAW.BUSINESS_LANDING)
--     CALL CORE.MERGE_BUSINESS_TOPIC();(Output: RAW.BUSINESS_TOPIC_LANDING)
--     CALL CORE.MERGE_MEMBER();        (Output: RAW.MEMBER_LANDING)
--     CALL CORE.MERGE_BUSINESS_AUTHOR();(Output: RAW.BUSINESS_AUTHOR_LANDING)
-- All four are idempotent: running the same load twice changes nothing.
-- =====================================================================
USE ROLE PIPELINE_ROLE;
USE WAREHOUSE PIPELINE_WH;
USE DATABASE PARLIAMENT;

CREATE OR REPLACE PROCEDURE CORE.MERGE_BUSINESS()
RETURNS VARIANT
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
    run_id   VARCHAR;
    landed   INTEGER;
    inserted INTEGER DEFAULT 0;
    updated  INTEGER DEFAULT 0;
BEGIN
    SELECT MAX(RUN_ID), COUNT(*) INTO :run_id, :landed FROM RAW.BUSINESS_LANDING;

    MERGE INTO CORE.BUSINESS t
    USING (
        SELECT l.*,
               SHA2(CONCAT_WS('|',
                    COALESCE(SHORT_NUMBER, ''), COALESCE(BUSINESS_TYPE, ''), COALESCE(TITLE_DE, ''),
                    COALESCE(BODY_DE, ''), COALESCE(REASON_DE, ''), COALESCE(STATUS, ''),
                    COALESCE(DEPARTMENT, ''), COALESCE(COUNCIL, ''), COALESCE(TAG_NAMES, ''),
                    COALESCE(TO_VARCHAR(SUBMITTED_ON), ''))) AS ROW_HASH,
               SHA2(CONCAT_WS('|',
                    COALESCE(TITLE_DE, ''), COALESCE(BODY_DE, ''), COALESCE(REASON_DE, ''))) AS CONTENT_HASH
        FROM RAW.BUSINESS_LANDING l
        WHERE BUSINESS_ID IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (PARTITION BY BUSINESS_ID ORDER BY MODIFIED_AT DESC NULLS LAST) = 1
    ) s
    ON t.BUSINESS_ID = s.BUSINESS_ID
    WHEN MATCHED AND t.ROW_HASH <> s.ROW_HASH THEN UPDATE SET
        t.SHORT_NUMBER = s.SHORT_NUMBER, t.BUSINESS_TYPE = s.BUSINESS_TYPE,
        t.BUSINESS_TYPE_NAME = s.BUSINESS_TYPE_NAME, t.TITLE_DE = s.TITLE_DE,
        t.BODY_DE = s.BODY_DE, t.REASON_DE = s.REASON_DE, t.STATUS = s.STATUS,
        t.DEPARTMENT = s.DEPARTMENT, t.COUNCIL = s.COUNCIL, t.TAG_IDS = s.TAG_IDS,
        t.TAG_NAMES = s.TAG_NAMES, t.TAG_COUNT = s.TAG_COUNT, t.BODY_CHARS = s.BODY_CHARS,
        t.SUBMITTED_ON = s.SUBMITTED_ON, t.MODIFIED_AT = s.MODIFIED_AT, t.SOURCE_URL = s.SOURCE_URL,
        t.ROW_HASH = s.ROW_HASH, t.CONTENT_HASH = s.CONTENT_HASH,
        t.LAST_CHANGED_AT = CURRENT_TIMESTAMP(), t.LAST_RUN_ID = s.RUN_ID
    WHEN NOT MATCHED THEN INSERT (
        BUSINESS_ID, SHORT_NUMBER, BUSINESS_TYPE, BUSINESS_TYPE_NAME, TITLE_DE, BODY_DE, REASON_DE,
        STATUS, DEPARTMENT, COUNCIL, TAG_IDS, TAG_NAMES, TAG_COUNT, BODY_CHARS, SUBMITTED_ON,
        MODIFIED_AT, SOURCE_URL, ROW_HASH, CONTENT_HASH, FIRST_LOADED_AT, LAST_CHANGED_AT, LAST_RUN_ID)
    VALUES (
        s.BUSINESS_ID, s.SHORT_NUMBER, s.BUSINESS_TYPE, s.BUSINESS_TYPE_NAME, s.TITLE_DE, s.BODY_DE, s.REASON_DE,
        s.STATUS, s.DEPARTMENT, s.COUNCIL, s.TAG_IDS, s.TAG_NAMES, s.TAG_COUNT, s.BODY_CHARS, s.SUBMITTED_ON,
        s.MODIFIED_AT, s.SOURCE_URL, s.ROW_HASH, s.CONTENT_HASH, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), s.RUN_ID);

    SELECT "number of rows inserted", "number of rows updated"
      INTO :inserted, :updated
      FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()));

    INSERT INTO OPS.LOAD_LOG (RUN_ID, TARGET_TABLE, ROWS_LANDED, ROWS_INSERTED, ROWS_UPDATED, ROWS_DELETED)
    VALUES (:run_id, 'CORE.BUSINESS', :landed, :inserted, :updated, 0);

    -- Alteryx's ODBC session runs with AUTOCOMMIT off: every write in a procedure
    -- must be committed before it returns, or Snowflake rolls it back.
    COMMIT;
    RETURN OBJECT_CONSTRUCT('table', 'CORE.BUSINESS', 'run_id', run_id,
                            'landed', landed, 'inserted', inserted, 'updated', updated);
END;
$$;

-- Topics: replace the full topic set of every business that was landed,
-- so a tag removed at the source is also removed here.
CREATE OR REPLACE PROCEDURE CORE.MERGE_BUSINESS_TOPIC()
RETURNS VARIANT
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
    run_id   VARCHAR;
    landed   INTEGER;
    deleted  INTEGER DEFAULT 0;
    inserted INTEGER DEFAULT 0;
BEGIN
    SELECT MAX(RUN_ID), COUNT(*) INTO :run_id, :landed FROM RAW.BUSINESS_TOPIC_LANDING;
    BEGIN TRANSACTION;
        DELETE FROM CORE.BUSINESS_TOPIC
         WHERE BUSINESS_ID IN (SELECT DISTINCT BUSINESS_ID FROM RAW.BUSINESS_TOPIC_LANDING);
        deleted := SQLROWCOUNT;
        INSERT INTO CORE.BUSINESS_TOPIC (BUSINESS_ID, TAG_ID, TOPIC_DE, TOPIC_EN, LAST_RUN_ID)
        SELECT BUSINESS_ID, TAG_ID, ANY_VALUE(TOPIC_DE), ANY_VALUE(TOPIC_EN), ANY_VALUE(RUN_ID)
          FROM RAW.BUSINESS_TOPIC_LANDING
         WHERE BUSINESS_ID IS NOT NULL AND TAG_ID IS NOT NULL
         GROUP BY BUSINESS_ID, TAG_ID;
        inserted := SQLROWCOUNT;
    COMMIT;
    INSERT INTO OPS.LOAD_LOG (RUN_ID, TARGET_TABLE, ROWS_LANDED, ROWS_INSERTED, ROWS_UPDATED, ROWS_DELETED)
    VALUES (:run_id, 'CORE.BUSINESS_TOPIC', :landed, :inserted, 0, :deleted);
    -- Alteryx's ODBC session runs with AUTOCOMMIT off: every write in a procedure
    -- must be committed before it returns, or Snowflake rolls it back.
    COMMIT;
    RETURN OBJECT_CONSTRUCT('table', 'CORE.BUSINESS_TOPIC', 'run_id', run_id,
                            'landed', landed, 'inserted', inserted, 'deleted', deleted);
END;
$$;

CREATE OR REPLACE PROCEDURE CORE.MERGE_MEMBER()
RETURNS VARIANT
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
    run_id   VARCHAR;
    landed   INTEGER;
    inserted INTEGER DEFAULT 0;
    updated  INTEGER DEFAULT 0;
BEGIN
    SELECT MAX(RUN_ID), COUNT(*) INTO :run_id, :landed FROM RAW.MEMBER_LANDING;
    MERGE INTO CORE.MEMBER t
    USING (
        SELECT * FROM RAW.MEMBER_LANDING
        WHERE MEMBER_ID IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (PARTITION BY MEMBER_ID ORDER BY ACTIVE DESC) = 1
    ) s
    ON t.MEMBER_ID = s.MEMBER_ID
    WHEN MATCHED AND (
           t.PARTY IS DISTINCT FROM s.PARTY OR t.PARL_GROUP IS DISTINCT FROM s.PARL_GROUP
        OR t.CANTON IS DISTINCT FROM s.CANTON OR t.COUNCIL IS DISTINCT FROM s.COUNCIL
        OR t.ACTIVE IS DISTINCT FROM s.ACTIVE OR t.LAST_NAME IS DISTINCT FROM s.LAST_NAME) THEN UPDATE SET
        t.FIRST_NAME = s.FIRST_NAME, t.LAST_NAME = s.LAST_NAME, t.PARTY = s.PARTY,
        t.PARL_GROUP = s.PARL_GROUP, t.CANTON = s.CANTON, t.COUNCIL = s.COUNCIL,
        t.ACTIVE = s.ACTIVE, t.LAST_RUN_ID = s.RUN_ID, t.UPDATED_AT = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT
        (MEMBER_ID, FIRST_NAME, LAST_NAME, PARTY, PARL_GROUP, CANTON, COUNCIL, ACTIVE, LAST_RUN_ID, UPDATED_AT)
    VALUES
        (s.MEMBER_ID, s.FIRST_NAME, s.LAST_NAME, s.PARTY, s.PARL_GROUP, s.CANTON, s.COUNCIL, s.ACTIVE, s.RUN_ID, CURRENT_TIMESTAMP());

    SELECT "number of rows inserted", "number of rows updated"
      INTO :inserted, :updated
      FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()));
    INSERT INTO OPS.LOAD_LOG (RUN_ID, TARGET_TABLE, ROWS_LANDED, ROWS_INSERTED, ROWS_UPDATED, ROWS_DELETED)
    VALUES (:run_id, 'CORE.MEMBER', :landed, :inserted, :updated, 0);
    -- Alteryx's ODBC session runs with AUTOCOMMIT off: every write in a procedure
    -- must be committed before it returns, or Snowflake rolls it back.
    COMMIT;
    RETURN OBJECT_CONSTRUCT('table', 'CORE.MEMBER', 'run_id', run_id,
                            'landed', landed, 'inserted', inserted, 'updated', updated);
END;
$$;

CREATE OR REPLACE PROCEDURE CORE.MERGE_BUSINESS_AUTHOR()
RETURNS VARIANT
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
    run_id   VARCHAR;
    landed   INTEGER;
    deleted  INTEGER DEFAULT 0;
    inserted INTEGER DEFAULT 0;
BEGIN
    SELECT MAX(RUN_ID), COUNT(*) INTO :run_id, :landed FROM RAW.BUSINESS_AUTHOR_LANDING;
    BEGIN TRANSACTION;
        DELETE FROM CORE.BUSINESS_AUTHOR
         WHERE BUSINESS_ID IN (SELECT DISTINCT BUSINESS_ID FROM RAW.BUSINESS_AUTHOR_LANDING);
        deleted := SQLROWCOUNT;
        INSERT INTO CORE.BUSINESS_AUTHOR (BUSINESS_ID, AUTHOR_TYPE, MEMBER_ID, COMMITTEE_ID, PARTY, CANTON, LAST_RUN_ID)
        SELECT DISTINCT BUSINESS_ID, AUTHOR_TYPE, MEMBER_ID, COMMITTEE_ID, PARTY, CANTON, RUN_ID
          FROM RAW.BUSINESS_AUTHOR_LANDING
         WHERE BUSINESS_ID IS NOT NULL;
        inserted := SQLROWCOUNT;
    COMMIT;
    INSERT INTO OPS.LOAD_LOG (RUN_ID, TARGET_TABLE, ROWS_LANDED, ROWS_INSERTED, ROWS_UPDATED, ROWS_DELETED)
    VALUES (:run_id, 'CORE.BUSINESS_AUTHOR', :landed, :inserted, 0, :deleted);
    -- Alteryx's ODBC session runs with AUTOCOMMIT off: every write in a procedure
    -- must be committed before it returns, or Snowflake rolls it back.
    COMMIT;
    RETURN OBJECT_CONSTRUCT('table', 'CORE.BUSINESS_AUTHOR', 'run_id', run_id,
                            'landed', landed, 'inserted', inserted, 'deleted', deleted);
END;
$$;
