-- =====================================================================
-- 06_notifications.sql  (run as PIPELINE_ROLE)
-- Two automated emails, both sent by Snowflake (SYSTEM$SEND_EMAIL):
--   1. DQ alert      Snowflake ALERT, hourly: emails as soon as an Alteryx
--                    run wrote a FAIL row to OPS.DQ_RESULTS.
--   2. Daily digest  TASK, 07:00 Zurich on weekdays: new items that Cortex
--                    flagged as AI / data / digitalisation, with English
--                    title + one-line summary + link.
-- =====================================================================
USE ROLE PIPELINE_ROLE;
USE WAREHOUSE PIPELINE_WH;
USE DATABASE PARLIAMENT;

CREATE OR REPLACE FUNCTION OPS.HTML_ESC(S VARCHAR)
RETURNS VARCHAR
AS
$$
    REPLACE(REPLACE(REPLACE(COALESCE(S, ''), '&', '&amp;'), '<', '&lt;'), '>', '&gt;')
$$;

-- 1. Data-quality alert --------------------------------------------------
CREATE OR REPLACE PROCEDURE OPS.SEND_DQ_ALERT()
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
    recipient VARCHAR;
    subject   VARCHAR;
    html      VARCHAR;
    n         INTEGER;
BEGIN
    SELECT VALUE INTO :recipient FROM OPS.CONFIG WHERE KEY = 'NOTIFY_EMAIL';
    SELECT COUNT(*),
           LISTAGG('<tr><td>' || OPS.HTML_ESC(ENTITY) || '</td><td>' || OPS.HTML_ESC(CHECK_NAME)
                   || '</td><td>' || COALESCE(TO_VARCHAR(EXPECTED), '') || '</td><td>'
                   || COALESCE(TO_VARCHAR(ACTUAL), '') || '</td><td>' || OPS.HTML_ESC(DETAIL) || '</td></tr>', '')
      INTO :n, :html
      FROM MART.V_DQ_LATEST
     WHERE STATUS = 'FAIL';
    IF (n = 0) THEN
        RETURN 'no failing checks';
    END IF;
    subject := 'Parliament pipeline: ' || n || ' data-quality check(s) failed';
    html := '<h3>' || subject || '</h3><table border="1" cellpadding="4" cellspacing="0">'
         || '<tr><th>Entity</th><th>Check</th><th>Expected</th><th>Actual</th><th>Detail</th></tr>'
         || html || '</table><p>Rejected rows: PARLIAMENT.OPS.DQ_REJECTS</p>';
    CALL SYSTEM$SEND_EMAIL('PIPELINE_EMAIL', :recipient, :subject, :html, 'text/html');
    INSERT INTO OPS.NOTIFICATION_LOG (KIND, ITEMS, RECIPIENT, SUBJECT) VALUES ('dq_alert', :n, :recipient, :subject);
    RETURN 'sent: ' || subject;
END;
$$;

CREATE OR REPLACE ALERT OPS.A_DQ_FAILED
    WAREHOUSE = PIPELINE_WH
    SCHEDULE = '60 MINUTE'
    IF (EXISTS (
        SELECT 1 FROM OPS.DQ_RESULTS
         WHERE STATUS = 'FAIL'
           AND INSERTED_AT > COALESCE(SNOWFLAKE.ALERT.LAST_SUCCESSFUL_SCHEDULED_TIME(),
                                      DATEADD('hour', -1, CURRENT_TIMESTAMP()))))
    THEN
        CALL OPS.SEND_DQ_ALERT();

-- 2. Daily watchlist digest --------------------------------------------
CREATE OR REPLACE PROCEDURE OPS.SEND_DIGEST()
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
    recipient VARCHAR;
    last_sent TIMESTAMP_LTZ;
    n         INTEGER;
    items     VARCHAR;
    subject   VARCHAR;
    html      VARCHAR;
BEGIN
    SELECT VALUE INTO :recipient FROM OPS.CONFIG WHERE KEY = 'NOTIFY_EMAIL';
    SELECT COALESCE(MAX(SENT_AT), '2000-01-01'::TIMESTAMP_LTZ) INTO :last_sent
      FROM OPS.NOTIFICATION_LOG WHERE KIND = 'digest';

    SELECT COUNT(*) INTO :n FROM MART.V_WATCHLIST WHERE ENRICHED_AT > :last_sent;
    IF (n = 0) THEN
        RETURN 'nothing new since ' || TO_VARCHAR(last_sent);
    END IF;

    SELECT LISTAGG('<li><a href="' || SOURCE_URL || '">' || OPS.HTML_ESC(SHORT_NUMBER) || ' '
                   || OPS.HTML_ESC(COALESCE(TITLE_EN, TITLE_DE)) || '</a> <small>('
                   || OPS.HTML_ESC(BUSINESS_TYPE_NAME) || ', ' || COALESCE(OPS.HTML_ESC(PARTY), 'n/a') || ', '
                   || TO_VARCHAR(SUBMITTED_ON) || ')</small><br>' || OPS.HTML_ESC(SUMMARY_EN) || '</li>', '')
             WITHIN GROUP (ORDER BY SUBMITTED_ON DESC)
      INTO :items
      FROM (SELECT * FROM MART.V_WATCHLIST WHERE ENRICHED_AT > :last_sent
             ORDER BY SUBMITTED_ON DESC LIMIT 15);

    subject := 'Swiss Parliament watchlist: ' || n || ' new AI / digital item(s)';
    html := '<h3>' || subject || '</h3><ul>' || items || '</ul>'
         || IFF(n > 15, '<p>... and ' || (n - 15) || ' more in MART.V_WATCHLIST.</p>', '')
         || '<p><small>Titles translated and summarised by Snowflake Cortex (AI_TRANSLATE, AI_COMPLETE); '
         || 'selected by AI_FILTER.</small></p>';
    CALL SYSTEM$SEND_EMAIL('PIPELINE_EMAIL', :recipient, :subject, :html, 'text/html');
    INSERT INTO OPS.NOTIFICATION_LOG (KIND, ITEMS, RECIPIENT, SUBJECT) VALUES ('digest', :n, :recipient, :subject);
    RETURN 'sent: ' || subject;
END;
$$;

CREATE OR REPLACE TASK OPS.T_DAILY_DIGEST
    WAREHOUSE = PIPELINE_WH
    SCHEDULE = 'USING CRON 0 7 * * MON-FRI Europe/Zurich'
AS
    CALL OPS.SEND_DIGEST();

ALTER ALERT OPS.A_DQ_FAILED RESUME;
ALTER TASK OPS.T_DAILY_DIGEST RESUME;
