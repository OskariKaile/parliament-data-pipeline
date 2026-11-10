-- =====================================================================
-- 07_streamlit.sql  (run as PIPELINE_ROLE)
-- Deploys the dashboard as a Streamlit in Snowflake app.
-- tools/sf.py uploads streamlit/streamlit_app.py to the stage first:
--   PUT file://snowflake/streamlit/streamlit_app.py @MART.APP_STAGE/monitor
--       AUTO_COMPRESS = FALSE OVERWRITE = TRUE;
-- =====================================================================
USE ROLE PIPELINE_ROLE;
USE WAREHOUSE PIPELINE_WH;
USE DATABASE PARLIAMENT;

CREATE STAGE IF NOT EXISTS MART.APP_STAGE
  DIRECTORY = (ENABLE = TRUE)
  COMMENT = 'Streamlit app source';

CREATE OR REPLACE STREAMLIT MART.PARLIAMENT_MONITOR
  FROM '@PARLIAMENT.MART.APP_STAGE/monitor'
  MAIN_FILE = 'streamlit_app.py'
  QUERY_WAREHOUSE = PIPELINE_WH
  TITLE = 'Swiss Parliament Monitor'
  COMMENT = 'Watchlist, topics, parties, Cortex eval and pipeline health';
