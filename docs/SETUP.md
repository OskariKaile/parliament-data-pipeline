# Setup: from zero to a running pipeline

About 45 minutes the first time, mostly installs.

## 1. Accounts and installs

| What | Where | Notes |
|---|---|---|
| Alteryx One trial (30 days) | https://www.alteryx.com/trial | Business email. Download the **Alteryx One app** from the Download Center; it installs Designer. In Designer: *Sign In to Your Trial*. |
| Snowflake trial (30 days, $400 credit) | https://signup.snowflake.com | Enterprise edition, AWS, Europe (Frankfurt). Sign up with the address in `OPS.CONFIG` `NOTIFY_EMAIL` (emails only go to verified users of the account). **Cortex AI is off on trial accounts until a credit card is added** (Admin → Billing). Adding a card does not end the trial; usage still comes out of the free credit. |
| Snowflake ODBC driver (Windows 64-bit) | https://developers.snowflake.com/odbc/ | Alteryx writes to Snowflake through it. |
| Python packages | `pip install requests snowflake-connector-python cryptography` | For `tools/`. |

## 2. Snowflake bootstrap (once, as ACCOUNTADMIN)

1. In Snowsight open a new SQL worksheet, paste [snowflake/00_bootstrap_admin.sql](../snowflake/00_bootstrap_admin.sql) and *Run All*.
2. The last statement prints `ACCOUNT_IDENTIFIER` (e.g. `ABCDEFG-XY12345`). Put it in `.env` in the repo root:
   ```
   SNOWFLAKE_ACCOUNT=ABCDEFG-XY12345
   ```
3. Profile (bottom left) → verify your email address. `SYSTEM$SEND_EMAIL` only sends to verified addresses.

The service user `PIPELINE_SVC` signs in with an RSA key pair (`secrets/rsa_key.p8`, git-ignored). It has no password, so there is no MFA prompt on every ODBC connection. Regenerate the key with:

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out secrets/rsa_key.p8
openssl rsa -in secrets/rsa_key.p8 -pubout -out secrets/rsa_key.pub
```

## 3. Snowflake objects

```bash
python tools/sf.py run snowflake/01_objects.sql snowflake/02_merge_procedures.sql snowflake/03_cortex_enrichment.sql
```

## 4. ODBC DSN for Alteryx

A user DSN named `snowflake_parliament` (PowerShell). Driver 4.x registers itself as `Snowflake ODBC` (older versions: `SnowflakeDSIIDriver`). `Add-OdbcDsn` refuses to store `uid`, so the user name is written to the DSN's registry key afterwards, which is what the ODBC settings window does too:

```powershell
Add-OdbcDsn -Name "snowflake_parliament" -DriverName "Snowflake ODBC" -DsnType "User" -Platform "64-bit" -SetPropertyValue @("server=<ACCOUNT_IDENTIFIER>.snowflakecomputing.com","database=PARLIAMENT","schema=RAW","warehouse=PIPELINE_WH","role=PIPELINE_ROLE","authenticator=SNOWFLAKE_JWT","priv_key_file=<repo>\secrets\rsa_key.p8")
Set-ItemProperty -Path "HKCU:\Software\ODBC\ODBC.INI\snowflake_parliament" -Name uid -Value PIPELINE_SVC
```

Test it in *ODBC Data Sources (64-bit)* → `snowflake_parliament` → *Configure* → *Test*, or from PowerShell with `System.Data.Odbc.OdbcConnection "DSN=snowflake_parliament"`.

## 5. Run the Alteryx workflow

1. Open `alteryx/parliament_pipeline.yxmd` in Alteryx Designer.
2. Optional: *Workflow Configuration* → *Workflow* → *Constants* → `SinceDate` (default `2025-01-01`).
3. Run (Ctrl+R). Expect about 15 API calls and these Snowflake results (from `tools/reference_check.py`):

| Table | Rows |
|---|---|
| CORE.BUSINESS | 6,690 (6,694 received, 1 duplicate and 3 empty titles rejected) |
| CORE.BUSINESS_TOPIC | 15,648 |
| CORE.MEMBER | 272 |
| CORE.BUSINESS_AUTHOR | 6,422 |
| OPS.DQ_RESULTS | 3 × `extract_complete` PASS, 2 × WARN |

```bash
python tools/reference_check.py --compare   # Snowflake CORE vs the Python twin
python tools/sf.py status
```

## 6. Cortex, dashboard, emails

```bash
python tools/sf.py run snowflake/04_cortex_eval.sql          # 300-item AI_CLASSIFY evaluation
python tools/sf.py sql "CALL PARLIAMENT.AI.ENRICH(10000)"   # first backfill (all items)
python tools/sf.py sql "ALTER TASK PARLIAMENT.AI.T_ENRICH RESUME"
python tools/sf.py run snowflake/05_marts.sql snowflake/06_notifications.sql
python tools/sf.py streamlit
```

Snowsight → *Projects* → *Streamlit* → **Swiss Parliament Monitor**.

## Cost on the trial

- Warehouse: XSMALL, auto-suspend 60 s, resource monitor stops it at 20 credits a month.
- Cortex is billed per token, separately from the warehouse. The full backfill is roughly 6,700 items × 4 functions on cut-down text (titles + first 1,500/3,000 characters). Check *Admin → Cost management* after the eval run and before the backfill.
- After that only changed items are enriched (content hash), so daily runs cost almost nothing.
