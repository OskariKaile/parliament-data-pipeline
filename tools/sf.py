"""Tiny Snowflake runner for this project (key-pair auth as PIPELINE_SVC).

    python tools/sf.py run snowflake/01_objects.sql snowflake/02_merge_procedures.sql
    python tools/sf.py sql "SELECT * FROM PARLIAMENT.MART.V_KPIS"
    python tools/sf.py streamlit      # upload the app and (re)create it
    python tools/sf.py status         # row counts, last DQ results, task state

Needs .env with SNOWFLAKE_ACCOUNT=<org>-<account> and secrets/rsa_key.p8.
pip install snowflake-connector-python cryptography
"""
from __future__ import annotations

import sys
from pathlib import Path

import snowflake.connector
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]


def env() -> dict[str, str]:
    out = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def connect():
    key = serialization.load_pem_private_key((ROOT / "secrets" / "rsa_key.p8").read_bytes(), password=None)
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return snowflake.connector.connect(
        account=env()["SNOWFLAKE_ACCOUNT"], user="PIPELINE_SVC", private_key=der,
        role="PIPELINE_ROLE", warehouse="PIPELINE_WH", database="PARLIAMENT",
        session_parameters={"QUERY_TAG": "parliament-data-pipeline"},
    )


def show(cur, limit: int = 50) -> None:
    if cur.description is None:
        return
    cols = [c[0] for c in cur.description]
    rows = cur.fetchmany(limit)
    if not rows:
        print("(no rows)")
        return
    w = [min(60, max(len(c), *(len(str(r[i])) for r in rows))) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    for r in rows:
        print("  ".join(str(v)[:60].ljust(w[i]) for i, v in enumerate(r)))


def query_one(sql: str):
    with connect() as con:
        return con.cursor().execute(sql).fetchone()


def run_files(paths: list[str]) -> None:
    with connect() as con:
        for p in paths:
            print(f"\n=== {p}")
            # remove_comments: a comment after a file's last ';' would otherwise be sent as an empty statement
            for cur in con.execute_string(Path(p).read_text(encoding="utf-8"), remove_comments=True):
                first = cur.query.strip().splitlines()[0][:100]
                print(f"ok  {first}")
            show(cur)  # result of the file's last statement


def main(argv: list[str]) -> None:
    if not argv:
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "run":
        run_files(rest)
    elif cmd == "sql":
        with connect() as con:
            for cur in con.execute_string(" ".join(rest)):
                pass
            show(cur, 200)
    elif cmd == "streamlit":
        with connect() as con:
            con.cursor().execute("CREATE STAGE IF NOT EXISTS PARLIAMENT.MART.APP_STAGE DIRECTORY = (ENABLE = TRUE)")
            for name in ("streamlit_app.py", "environment.yml"):
                f = (ROOT / "snowflake" / "streamlit" / name).as_posix()
                con.cursor().execute(f"PUT 'file://{f}' @PARLIAMENT.MART.APP_STAGE/monitor "
                                     "AUTO_COMPRESS = FALSE OVERWRITE = TRUE")
        run_files([str(ROOT / "snowflake" / "07_streamlit.sql")])
    elif cmd == "status":
        with connect() as con:
            for sql in [
                """SELECT 'CORE.BUSINESS' T, COUNT(*) N FROM CORE.BUSINESS UNION ALL
                   SELECT 'CORE.BUSINESS_TOPIC', COUNT(*) FROM CORE.BUSINESS_TOPIC UNION ALL
                   SELECT 'CORE.MEMBER', COUNT(*) FROM CORE.MEMBER UNION ALL
                   SELECT 'CORE.BUSINESS_AUTHOR', COUNT(*) FROM CORE.BUSINESS_AUTHOR UNION ALL
                   SELECT 'AI.BUSINESS_ENRICHED', COUNT(*) FROM AI.BUSINESS_ENRICHED UNION ALL
                   SELECT 'AI.ENRICH_QUEUE', COUNT(*) FROM AI.ENRICH_QUEUE""",
                "SELECT ENTITY, CHECK_NAME, EXPECTED, ACTUAL, STATUS, INSERTED_AT FROM MART.V_DQ_LATEST ORDER BY 1, 2",
                "SHOW TASKS IN DATABASE PARLIAMENT",
            ]:
                print()
                show(con.cursor().execute(sql))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
