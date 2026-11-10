# Pipeline walkthrough

## Alteryx workflow, container by container

### 1 Extract: config-driven OData ingestion
| Tool | Why |
|---|---|
| **Text Input** (config) | One row per API entity: `ENTITY`, `FILTER`, `FIELDS`, `BASE_URL`, `PAGE_SIZE`. A new source is a new row, not a new branch. `{SINCE}` is replaced by the workflow constant `[User.SinceDate]`. |
| **Formula** | URL-encodes the filter, builds the `$count` URL. |
| **Download** | `GET /Entity/$count` → total rows. No `Accept` header: the API's firewall rejects `Accept: application/json` on `$count`. |
| **Formula + Select** | `TOTAL`, `N_PAGES = CEIL(TOTAL / PAGE_SIZE)`; drop the response columns before the second Download. |
| **Generate Rows** | `PAGE` from 0 while `PAGE < N_PAGES`: one row per page, the Alteryx way to paginate without a macro. |
| **Formula** | Page URL with `$select`, `$orderby=ID`, `$top`, `$skip`, `$format=json`. |
| **Download** | One call per page. |
| **Formula + Filter** | HTTP status from the headers; non-200 pages go to the reject branch. |
| **Select** | Keep only what JSON Parse needs, so the large URL and header strings are not copied onto 140k rows. |
| **JSON Parse** | Flattens each page into `JSON_Name` / `JSON_ValueString` rows. |
| **Formula + Filter** | Regex out the record index and field name (`d.12.Title` or `d.results.12.Title`; the API uses both shapes). Drop `__metadata` / `__deferred`. |
| **Cross Tab** | Back to one row per record, grouped by `ENTITY, TOTAL, REC_KEY`. |
| **Output** (.yxdb) | Local raw snapshot for debugging and replay. |

### 2 Business items + topics
Filter `ENTITY = 'Business'` → Formula (types, HTML → text, `/Date(ms)/` → date, `TAG_COUNT`, source URL, `RUN_ID` from `DateTimeStart()` so every output of one run shares it) → **Unique** on `BUSINESS_ID` → **Filter** DQ rule (id, title, date present) → landing layout.

Topics: **Text To Columns** splits `TAG_NAMES` on `|` *to rows* → **Join** with `reference/topics.csv` on the German name → tag id + English label. Unmatched topics on the left output mean the reference file is out of date, which is a `FAIL`.

### 3 Members + authors
Members: typed, deduped, DQ (id + name). Authors (`BusinessRole`, role 7 = author): `AUTHOR_TYPE` = member / committee / canton / parliamentary group. Member authors are **joined** to members for party and canton. The join's left output (author not in the member list) is kept and also logged, the orphan-key check.

### 4 Data quality
- Every reject branch (duplicates, missing fields, unknown topic, orphan author, HTTP error) is shaped into one layout by Formula + Select and unioned into `OPS.DQ_REJECTS`.
- **Summarize** per reason → `WARN` rows in `OPS.DQ_RESULTS` (`FAIL` for unknown topic).
- **extract_complete**: records received after the Cross Tab per entity vs the API's own `$count` → `PASS`/`FAIL`. This catches pages that silently came back short.

### 5 Load
Output Data over ODBC, *Delete Data & Append* into pre-created landing tables (fixed types, UPPER_CASE columns, so ODBC mapping is 1:1 with no quoted-identifier surprises). Each output's **Post Create SQL** calls the matching `CORE.MERGE_*` procedure, so CORE is updated as soon as that table lands.

## Snowflake design decisions

- **Landing + MERGE instead of writing straight into the final table.** Alteryx stays a simple writer. Upsert logic, hashing and logging live in SQL, where they are versioned and testable. Re-running a load is harmless.
- **Two hashes.** `ROW_HASH` (all columns) decides whether CORE is updated. `CONTENT_HASH` (title + texts) decides whether Cortex runs again. A status change ("submitted" → "dealt with") updates the row but does not pay for new AI calls.
- **Stream + triggered task** for Cortex: the AI step is decoupled from the Alteryx run, runs only when something changed, and works through a queue with a row cap.
- **Full extract, incremental enrichment.** Re-extracting 2025+ is 15 API calls and catches edits to old items. The expensive part (AI) is incremental.
- **Key-pair service user** (`TYPE = SERVICE`) for Alteryx and scripts: no password in the workflow, no MFA prompt on ODBC.
- **Cost guards**: XSMALL warehouse, 60 s auto-suspend, resource monitor, text cut to 1,500/3,000 characters before Cortex.
- **Emails from Snowflake** (`SYSTEM$SEND_EMAIL`), not SMTP credentials in the workflow. The DQ alert is a Snowflake ALERT on `OPS.DQ_RESULTS`; the digest is a CRON task.

## Testing

`tools/reference_check.py` re-implements the workflow logic in Python (same filters, same JSON naming, same regexes and DQ rules) and writes the numbers the Alteryx run must produce to `data/expected_counts.json`. `--compare` checks Snowflake against them. It found the Accept-header problem, the source duplicate and the empty titles before the first Alteryx run.

## What the first real runs taught me (Designer 2026.3, 7 Nov 2026)

The workflow is generated from `alteryx/build_workflow.py`, written before I had Designer installed. The first runs in Designer found these, all fixed in the generator:

| Run | Symptom in Designer | Cause | Fix |
|---|---|---|---|
| 1 | Download tools shown as `GenericTool` with a padlock; *entry point "AlteryxDownload" could not be found* | The Download tool lives in `AlteryxConnectorEngine.dll`, not the base plugins | Checked every plugin name against the sample workflows that ship with Designer; only Download was wrong |
| 1 | (silent risk) | Download settings used made-up tag names; without `*Unknown selected="False"` other columns could be sent as headers or query parameters | Copied the layout of Designer's own *Download and parse web data* sample |
| 2 | *Scoped transaction started in stored procedure is incomplete* on every Post-SQL `CALL` | Alteryx's ODBC session runs with AUTOCOMMIT off, so Snowflake requires a procedure to commit its own writes; the load-log INSERT came after the COMMIT | `COMMIT` before every `RETURN` |
| 2 | *invalid identifier '"Active"'* on the member output | Alteryx field names are case-insensitive: a new `ACTIVE` field overwrote the API's `Active` field and kept its spelling | Compute `IS_ACTIVE`, rename to `ACTIVE` in the Select |
| 2-4 | Business table: 1 row per INSERT, ~1 hour | The generic ODBC output batches rows by declared width; the text fields were declared 1 GB | Sized text fields to the data (title max 323, body 8,570, reason 12,060 chars) and capped at 16,000 in both Alteryx and the Python twin: ~9 rows per INSERT, ~6 minutes |

I also tried the **Snowflake Bulk loader** (`snowbl:`, local staging via PUT + COPY), the usual answer to slow ODBC loads. Designer 2026.3 with Snowflake ODBC driver 4.3 failed with *Data source name not found* both with the DSN and DSN-less, while the same strings work for `odbc:`. The likely cause is that the bulk loader expects the older driver name `SnowflakeDSIIDriver` (driver 4.x registers as `Snowflake ODBC`). On a client setup with the 3.x driver or DCM-managed connections, that is the first thing I would switch on.

**Streamlit in Snowflake (10 Nov 2026).** The dashboard did not open: *pyproject.toml file does not exist*. Since Snowflake's 2026_06 behaviour change, an app created without `RUNTIME_NAME` gets the new container runtime, which needs a compute pool and a `pyproject.toml`. The app was written for the warehouse runtime (`get_active_session()`, only built-in packages), so `07_streamlit.sql` now sets `RUNTIME_NAME = 'SYSTEM$WAREHOUSE_RUNTIME'`. The warehouse runtime's default Streamlit has no `st.column_config`, so `environment.yml` pins `streamlit=1.52.2` from the Snowflake conda channel.

## What I would do next in production

- Move the Snowflake connection from a DSN to Alteryx DCM with the key in a vault, and schedule the workflow on Alteryx Server / Alteryx One.
- Use the Snowflake bulk loader output (stage + COPY) instead of ODBC inserts once volumes grow.
- Wrap the paging pattern in a batch macro so other OData sources can reuse it.
- Add French and Italian titles (the API has all three languages) and compare Cortex translation quality against the official translations.
- Track evaluation results over time, so a model or prompt change shows up as a metric change before it ships.
