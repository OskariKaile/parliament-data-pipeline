-- =====================================================================
-- 03_cortex_enrichment.sql  (run as PIPELINE_ROLE)
-- Cortex AI on every new or changed business item:
--   AI_TRANSLATE  German title -> English
--   AI_COMPLETE   one-sentence English summary (model from OPS.CONFIG)
--   AI_COMPLETE   topics: same model, JSON schema with the 28 official topics
--                 (AI_CLASSIFY only as a fallback, see below)
--   AI_FILTER     "is this about AI / data / digitalisation / cyber?"
-- Flow: CORE.BUSINESS -> stream CORE.BUSINESS_CHANGES -> AI.ENRICH_QUEUE
--       -> AI.ENRICH(n) -> AI.BUSINESS_ENRICHED
-- Cost control: the AI functions only see rows whose CONTENT_HASH changed,
-- the text is cut to 3000 / 1500 characters, and a run takes max_rows.
-- Why topics come from AI_COMPLETE and not AI_CLASSIFY: in the 300-item
-- evaluation (04, docs/CORTEX_EVAL.md) the JSON-schema variant had the best F1
-- (0.653 vs 0.637) at about 1/12 of the credits per item. It returned NULL for
-- 5 of 300 items, so those rows get a second pass with AI_CLASSIFY.
-- =====================================================================
USE ROLE PIPELINE_ROLE;
USE WAREHOUSE PIPELINE_WH;
USE DATABASE PARLIAMENT;

CREATE TABLE IF NOT EXISTS AI.ENRICH_QUEUE (
    BUSINESS_ID NUMBER(38,0) NOT NULL PRIMARY KEY,
    QUEUED_AT   TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS AI.BUSINESS_ENRICHED (
    BUSINESS_ID      NUMBER(38,0) NOT NULL PRIMARY KEY,
    CONTENT_HASH     VARCHAR(64),
    TITLE_EN         VARCHAR,
    SUMMARY_EN       VARCHAR,
    TOPICS_PREDICTED ARRAY,
    IS_DIGITAL       BOOLEAN,
    SUMMARY_MODEL    VARCHAR(100),
    ENRICHED_AT      TIMESTAMP_LTZ
);

CREATE OR REPLACE PROCEDURE AI.ENRICH(MAX_ROWS NUMBER)
RETURNS VARIANT
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
    queued   INTEGER DEFAULT 0;
    batch    INTEGER DEFAULT 0;
    enriched INTEGER DEFAULT 0;
    left_over INTEGER DEFAULT 0;
    fallback INTEGER DEFAULT 0;
    model    VARCHAR;
BEGIN
    SELECT COALESCE(MAX(VALUE), 'llama3.1-70b') INTO :model FROM OPS.CONFIG WHERE KEY = 'SUMMARY_MODEL';

    -- 1. Consume the stream: every inserted/updated business goes to the queue.
    MERGE INTO AI.ENRICH_QUEUE q
    USING (SELECT DISTINCT BUSINESS_ID FROM CORE.BUSINESS_CHANGES WHERE METADATA$ACTION = 'INSERT') s
       ON q.BUSINESS_ID = s.BUSINESS_ID
    WHEN NOT MATCHED THEN INSERT (BUSINESS_ID) VALUES (s.BUSINESS_ID);
    queued := SQLROWCOUNT;

    -- 2. Pick the batch: queued rows whose text changed since the last enrichment.
    CREATE OR REPLACE TEMPORARY TABLE AI.ENRICH_BATCH AS
    SELECT b.BUSINESS_ID, b.CONTENT_HASH, b.TITLE_DE,
           LEFT(COALESCE(b.BODY_DE, ''), 3000) AS BODY_3000,
           LEFT(COALESCE(b.BODY_DE, ''), 1500) AS BODY_1500
      FROM AI.ENRICH_QUEUE q
      JOIN CORE.BUSINESS b ON b.BUSINESS_ID = q.BUSINESS_ID
      LEFT JOIN AI.BUSINESS_ENRICHED e ON e.BUSINESS_ID = b.BUSINESS_ID
     WHERE e.CONTENT_HASH IS DISTINCT FROM b.CONTENT_HASH
     ORDER BY b.SUBMITTED_ON DESC
     LIMIT :MAX_ROWS;
    SELECT COUNT(*) INTO :batch FROM AI.ENRICH_BATCH;

    -- 3. Call Cortex once per row and upsert the result.
    MERGE INTO AI.BUSINESS_ENRICHED t
    USING (
        SELECT
            BUSINESS_ID,
            CONTENT_HASH,
            AI_TRANSLATE(TITLE_DE, 'de', 'en') AS TITLE_EN,
            -- No summary for items that have only a title (elections, sessions, new petitions):
            -- in the first backfill the model filled those in with details that are not in the source.
            CASE WHEN BODY_3000 <> '' THEN AI_COMPLETE(
                :model,
                'You summarise Swiss parliamentary business for an English-speaking analyst. '
                || 'In ONE sentence of at most 30 words, say what this item asks for or proposes. '
                || 'Use only facts from the text. Reply with the sentence only.'
                || '\n\nTitle: ' || TITLE_DE
                || '\n\nText (German): ' || BODY_3000,
                {'temperature': 0, 'max_tokens': 120}
            ) END AS SUMMARY_EN,
            -- same prompt and schema as variant v4_complete_json in 04_cortex_eval.sql
            AI_COMPLETE(
                model => :model,
                prompt => 'Pick the policy topics (usually 2 or 3) of this Swiss parliamentary item. '
                       || 'Allowed topics:\n- Government and politics\n- International relations\n- Security and defence\n- European policy\n- Law (general)\n- Economy\n- Public finance\n- Social issues\n- Education\n- Media and communications\n- Science and research\n- Employment and labour\n- Transport\n- Environment\n- Agriculture\n- Energy\n- Parliament\n- Civil law\n- Criminal law\n- Courts and judiciary\n- International law\n- Human rights\n- Taxation\n- Migration\n- Culture\n- Social security\n- Health\n- Spatial planning and housing\n\nItem (German):\n'
                       || TITLE_DE || '\n' || BODY_1500,
                model_parameters => {'temperature': 0, 'max_tokens': 200},
                response_format => {
                    'type': 'json',
                    'schema': {
                        'type': 'object',
                        'properties': {
                            'topics': {
                                'type': 'array',
                                'items': {'type': 'string',
                                          'enum': ['Government and politics', 'International relations', 'Security and defence',
                                                   'European policy', 'Law (general)', 'Economy', 'Public finance', 'Social issues',
                                                   'Education', 'Media and communications', 'Science and research',
                                                   'Employment and labour', 'Transport', 'Environment', 'Agriculture', 'Energy',
                                                   'Parliament', 'Civil law', 'Criminal law', 'Courts and judiciary',
                                                   'International law', 'Human rights', 'Taxation', 'Migration', 'Culture',
                                                   'Social security', 'Health', 'Spatial planning and housing']}
                            }
                        },
                        'required': ['topics']
                    }
                }
            ):topics::ARRAY AS TOPICS_PREDICTED,
            AI_FILTER(PROMPT(
                'Does this Swiss parliamentary item mainly concern artificial intelligence, data, '
                || 'digitalisation, IT systems or cybersecurity? Title: {0} Text: {1}',
                TITLE_DE, BODY_1500)) AS IS_DIGITAL
        FROM AI.ENRICH_BATCH
    ) s
    ON t.BUSINESS_ID = s.BUSINESS_ID
    WHEN MATCHED THEN UPDATE SET
        t.CONTENT_HASH = s.CONTENT_HASH, t.TITLE_EN = s.TITLE_EN, t.SUMMARY_EN = TRIM(s.SUMMARY_EN),
        t.TOPICS_PREDICTED = s.TOPICS_PREDICTED, t.IS_DIGITAL = s.IS_DIGITAL,
        t.SUMMARY_MODEL = :model, t.ENRICHED_AT = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT
        (BUSINESS_ID, CONTENT_HASH, TITLE_EN, SUMMARY_EN, TOPICS_PREDICTED, IS_DIGITAL, SUMMARY_MODEL, ENRICHED_AT)
    VALUES
        (s.BUSINESS_ID, s.CONTENT_HASH, s.TITLE_EN, TRIM(s.SUMMARY_EN), s.TOPICS_PREDICTED, s.IS_DIGITAL, :model, CURRENT_TIMESTAMP());
    enriched := SQLROWCOUNT;

    -- 3b. Fallback: the JSON-schema call returns NULL for ~2% of items. Those get AI_CLASSIFY
    --     (the eval's v3 setup). A separate UPDATE, so AI_CLASSIFY only runs for the failed rows.
    UPDATE AI.BUSINESS_ENRICHED t
       SET TOPICS_PREDICTED = AI_CLASSIFY(
                b.TITLE_DE || '\n' || b.BODY_1500,
                ['Government and politics', 'International relations', 'Security and defence', 'European policy',
                 'Law (general)', 'Economy', 'Public finance', 'Social issues', 'Education',
                 'Media and communications', 'Science and research', 'Employment and labour', 'Transport',
                 'Environment', 'Agriculture', 'Energy', 'Parliament', 'Civil law', 'Criminal law',
                 'Courts and judiciary', 'International law', 'Human rights', 'Taxation', 'Migration',
                 'Culture', 'Social security', 'Health', 'Spatial planning and housing'],
                {'output_mode': 'multi',
                 'task_description': 'Assign the policy topics of a Swiss parliamentary item (German text). Most items have two or three topics.'}
            ):labels::ARRAY
      FROM AI.ENRICH_BATCH b
     WHERE t.BUSINESS_ID = b.BUSINESS_ID AND t.TOPICS_PREDICTED IS NULL;
    fallback := SQLROWCOUNT;

    -- 4. Dequeue everything that is now up to date (processed or unchanged).
    DELETE FROM AI.ENRICH_QUEUE
     WHERE BUSINESS_ID IN (
        SELECT e.BUSINESS_ID
          FROM AI.BUSINESS_ENRICHED e
          JOIN CORE.BUSINESS b ON b.BUSINESS_ID = e.BUSINESS_ID
         WHERE e.CONTENT_HASH = b.CONTENT_HASH);

    INSERT INTO OPS.LOAD_LOG (RUN_ID, TARGET_TABLE, ROWS_LANDED, ROWS_INSERTED, ROWS_UPDATED, ROWS_DELETED)
    SELECT TO_VARCHAR(CURRENT_TIMESTAMP(), 'YYYYMMDDHH24MISS'), 'AI.BUSINESS_ENRICHED', :batch, :enriched, 0, 0;

    SELECT COUNT(*) INTO :left_over FROM AI.ENRICH_QUEUE;
    -- Alteryx's ODBC session runs with AUTOCOMMIT off: every write in a procedure
    -- must be committed before it returns, or Snowflake rolls it back.
    COMMIT;
    RETURN OBJECT_CONSTRUCT('queued_from_stream', queued, 'batch', batch, 'enriched', enriched,
                            'topic_fallback', fallback, 'still_queued', left_over, 'model', model);
END;
$$;

-- Triggered task: runs by itself whenever the stream has new rows
-- (i.e. right after an Alteryx load changed something). Created SUSPENDED:
-- do the evaluation (04) and the first backfill by hand, then RESUME.
CREATE OR REPLACE TASK AI.T_ENRICH
    WAREHOUSE = PIPELINE_WH
    WHEN SYSTEM$STREAM_HAS_DATA('CORE.BUSINESS_CHANGES')
AS
    CALL AI.ENRICH(2000);

-- After the first manual backfill:
--   CALL AI.ENRICH(10000);
--   ALTER TASK AI.T_ENRICH RESUME;
