"""Bundle the project files into preview/index.html (a local, read-only preview).

Only the files listed below are copied in, so the server never exposes
secrets/ or .env.

    python tools/build_preview.py
    python -m http.server 4215 --bind 127.0.0.1 --directory preview
"""
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "README.md", "docs/SETUP.md", "docs/PIPELINE.md",
    "alteryx/parliament_pipeline.yxmd", "alteryx/build_workflow.py",
    *sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "snowflake").glob("*.sql")),
    "snowflake/streamlit/streamlit_app.py",
    "tools/reference_check.py", "tools/sf.py", "tools/gen_eval_sql.py", "reference/topics.csv",
]


def load_json(rel: str):
    p = ROOT / rel
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


bundle = {
    "files": {f: (ROOT / f).read_text(encoding="utf-8") for f in FILES if (ROOT / f).exists()},
    "expected": load_json("data/expected_counts.json"),
    "profile": load_json("data/profile.json"),
}
payload = json.dumps(bundle, ensure_ascii=False).replace("</", "<\\/")
html = (ROOT / "tools" / "preview_template.html").read_text(encoding="utf-8")
html = html.replace("__BUNDLE__", payload).replace("__BUILT__", datetime.now().strftime("%d %b %Y %H:%M"))
out = ROOT / "preview" / "index.html"
out.parent.mkdir(exist_ok=True)
out.write_text(html, encoding="utf-8")
print(f"wrote {out} ({len(html) // 1024} KB, {len(bundle['files'])} files)")
