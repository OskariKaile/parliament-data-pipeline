"""Python twin of the Alteryx workflow, used as a test oracle.

It calls the same API with the same filters, flattens the JSON the way the
Alteryx JSON Parse tool names it (d.<i>.<Field> / d.results.<i>.<Field>),
applies the same regexes, cleaning and DQ rules, and writes the numbers the
Alteryx run must reproduce to data/expected_counts.json.

    python tools/reference_check.py            # full run (~15 API calls)
    python tools/reference_check.py --compare  # + compare with Snowflake (needs tools/sf.py setup)
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://ws.parlament.ch/odata.svc"
SINCE = "2025-01-01"
# No Accept header: the API firewall rejects "Accept: application/json" on $count (text/plain).
HEADERS = {"User-Agent": "parliament-data-pipeline (portfolio project)"}
ENTITIES = {
    "Business": ("Language eq 'DE' and SubmissionDate ge datetime'{SINCE}T00:00:00'",
                 "ID,BusinessShortNumber,BusinessTypeAbbreviation,BusinessTypeName,Title,Description,SubmittedText,"
                 "ReasonText,InitialSituation,BusinessStatusText,ResponsibleDepartmentAbbreviation,"
                 "SubmissionCouncilAbbreviation,Tags,TagNames,SubmissionDate,Modified"),
    "BusinessRole": ("Language eq 'DE' and Role eq 7 and BusinessSubmissionDate ge datetime'{SINCE}T00:00:00'",
                     "ID,BusinessNumber,MemberCouncilNumber,CommitteeNumber,CantonNumber,ParlGroupNumber,Role"),
    "MemberCouncil": ("Language eq 'DE' and (DateLeaving eq null or DateLeaving ge datetime'{SINCE}T00:00:00')",
                      "ID,FirstName,LastName,PartyAbbreviation,ParlGroupAbbreviation,CantonAbbreviation,"
                      "CouncilAbbreviation,Active"),
}
PAGE_SIZE = 1000

# the same patterns as the Alteryx Formula / Filter tools
RE_REC = re.compile(r"^d\.(?:results\.)?(\d+)\..*$")
RE_FIELD = re.compile(r"^d\.(?:results\.)?\d+\.")
RE_TOP = re.compile(r"^d\.(results\.)?\d+\.[A-Za-z]+$")


def enc(f: str) -> str:
    return f.replace("{SINCE}", SINCE).replace(" ", "%20").replace("'", "%27")


def get(url: str) -> requests.Response:
    for attempt in range(4):
        r = requests.get(url, headers=HEADERS, timeout=120)
        if r.status_code == 200:
            return r
        time.sleep(2 ** attempt)
    r.raise_for_status()
    return r


def flatten(obj, prefix: str = "d"):
    """Yield (JSON_Name, value-as-string) like Alteryx JSON Parse (single string field)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from flatten(v, f"{prefix}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from flatten(v, f"{prefix}.{i}")
    else:
        yield prefix, None if obj is None else str(obj)


def clean_html(s: str | None) -> str | None:
    if s is None:
        return None
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return re.sub(r"\s+", " ", s).strip()[:16000]  # TEXT_CAP in alteryx/build_workflow.py


def epoch(s: str | None) -> datetime | None:
    if not s:
        return None
    ms = int(re.sub(r"[^0-9]", "", s))
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc)


def extract() -> dict[str, list[dict]]:
    records: dict[str, list[dict]] = {}
    totals: dict[str, int] = {}
    received: dict[str, int] = {}
    for entity, (flt, fields) in ENTITIES.items():
        total = int(get(f"{BASE}/{entity}/$count?$filter={enc(flt)}").text.strip())
        totals[entity] = total
        rows: dict[int, dict] = {}
        for page in range(math.ceil(total / PAGE_SIZE)):
            url = (f"{BASE}/{entity}?$filter={enc(flt)}&$select={fields}&$orderby=ID"
                   f"&$top={PAGE_SIZE}&$skip={page * PAGE_SIZE}&$format=json")
            for name, val in flatten(get(url).json()["d"]):
                if not RE_TOP.match(name):
                    continue
                key = page * PAGE_SIZE + int(RE_REC.sub(r"\1", name))
                rows.setdefault(key, {})[RE_FIELD.sub("", name)] = val
        records[entity] = list(rows.values())
        received[entity] = len(rows)
        print(f"{entity:14} $count={total:6}  records={len(rows):6}")
    records["_totals"] = totals  # type: ignore[assignment]
    records["_received"] = received  # type: ignore[assignment]
    return records


def main() -> None:
    data = extract()
    totals = data.pop("_totals")
    received = data.pop("_received")
    topics = {r["TOPIC_DE"]: r for r in csv.DictReader(open(ROOT / "reference" / "topics.csv", encoding="utf-8"))}

    # Business
    seen, dup, bad, valid = set(), 0, 0, []
    for r in data["Business"]:
        bid = int(r["ID"]) if r.get("ID") else None
        if bid in seen:
            dup += 1
            continue
        seen.add(bid)
        body = r.get("SubmittedText") or r.get("InitialSituation") or r.get("Description")
        row = {
            "BUSINESS_ID": bid, "TITLE_DE": (r.get("Title") or "").strip(), "BODY_DE": clean_html(body),
            "TAG_NAMES": r.get("TagNames"), "SUBMITTED_ON": epoch(r.get("SubmissionDate")),
            "SHORT_NUMBER": r.get("BusinessShortNumber"), "BUSINESS_TYPE_NAME": r.get("BusinessTypeName"),
            "STATUS": r.get("BusinessStatusText"),
        }
        if bid is None or not row["TITLE_DE"] or row["SUBMITTED_ON"] is None:
            bad += 1
            continue
        valid.append(row)
    topic_rows = [(b["BUSINESS_ID"], t.strip()) for b in valid for t in (b["TAG_NAMES"] or "").split("|") if t.strip()]
    unknown = Counter(t for _, t in topic_rows if t not in topics)

    # Members
    mem_ids = {int(m["ID"]) for m in data["MemberCouncil"] if m.get("ID") and m.get("LastName")}
    mem_active = sum(1 for m in data["MemberCouncil"]
                     if m.get("ID") and m.get("LastName") and str(m.get("Active")).lower() == "true")

    # Authors
    roles = data["BusinessRole"]
    types = Counter()
    orphans = 0
    for r in roles:
        if r.get("MemberCouncilNumber"):
            types["member"] += 1
            if int(r["MemberCouncilNumber"]) not in mem_ids:
                orphans += 1
        elif r.get("CommitteeNumber"):
            types["committee"] += 1
        elif r.get("CantonNumber"):
            types["canton"] += 1
        elif r.get("ParlGroupNumber"):
            types["parl_group"] += 1
        else:
            types["other"] += 1

    lens = sorted(len(b["BODY_DE"] or "") for b in valid)
    expected = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "since": SINCE,
        "api_count": totals,
        "business": {"valid": len(valid), "duplicates": dup, "rejected_missing_fields": bad,
                     "with_topics": sum(1 for b in valid if b["TAG_NAMES"]),
                     "body_chars_p50": lens[len(lens) // 2], "body_chars_max": lens[-1]},
        "business_topic": {"rows": len(topic_rows), "unknown_topics": dict(unknown)},
        "member": {"valid": len(mem_ids), "active": mem_active},
        "business_author": {"rows": len(roles), "by_type": dict(types), "orphan_member_authors": orphans},
        # same rule as the Alteryx 'extract_complete' check: records received == API $count
        "extract_complete": {e: received[e] == totals[e] for e in totals},
    }
    out = ROOT / "data" / "expected_counts.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(expected, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(expected, indent=2, ensure_ascii=False))

    # Profile + sample rows for the local preview page (tools/build_preview.py)
    members = {int(m["ID"]): m for m in data["MemberCouncil"] if m.get("ID")}
    party = Counter(members[int(r["MemberCouncilNumber"])].get("PartyAbbreviation") or "none"
                    for r in roles if r.get("MemberCouncilNumber") and int(r["MemberCouncilNumber"]) in members)
    en = {r["TOPIC_DE"]: r["TOPIC_EN"] for r in topics.values()}
    recent = sorted(valid, key=lambda b: (b["SUBMITTED_ON"], b["BUSINESS_ID"]), reverse=True)[:60]
    profile = {
        "per_month": dict(sorted(Counter(b["SUBMITTED_ON"].strftime("%Y-%m") for b in valid).items())),
        "per_type": dict(Counter(b["BUSINESS_TYPE_NAME"] for b in valid).most_common()),
        "per_topic_en": dict(Counter(en.get(t, t) for _, t in topic_rows).most_common()),
        "authors_per_party": dict(party.most_common()),
        "sample": [{
            "short_number": b["SHORT_NUMBER"], "type": b["BUSINESS_TYPE_NAME"],
            "submitted_on": b["SUBMITTED_ON"].strftime("%Y-%m-%d"), "status": b["STATUS"],
            "title_de": b["TITLE_DE"],
            "topics_en": [en.get(t.strip(), t.strip()) for t in (b["TAG_NAMES"] or "").split("|") if t.strip()],
            "body_de": (b["BODY_DE"] or "")[:400],
            "url": f"https://www.parlament.ch/de/ratsbetrieb/suche-curia-vista/geschaeft?AffairId={b['BUSINESS_ID']}",
        } for b in recent],
    }
    (ROOT / "data" / "profile.json").write_text(json.dumps(profile, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote data/profile.json ({len(profile['sample'])} sample rows)")

    if "--compare" in sys.argv:
        sys.path.insert(0, str(ROOT / "tools"))
        import sf  # noqa: E402
        got = sf.query_one("""SELECT (SELECT COUNT(*) FROM CORE.BUSINESS) B, (SELECT COUNT(*) FROM CORE.BUSINESS_TOPIC) T,
                                     (SELECT COUNT(*) FROM CORE.MEMBER) M, (SELECT COUNT(*) FROM CORE.BUSINESS_AUTHOR) A,
                                     (SELECT COUNT(*) FROM CORE.MEMBER WHERE ACTIVE) MA""")
        print("\nSnowflake CORE vs Python twin:")
        for label, sfv, py in [("business", got[0], len(valid)), ("business_topic", got[1], len(topic_rows)),
                               ("member", got[2], len(mem_ids)), ("business_author", got[3], len(roles)),
                               ("member_active", got[4], mem_active)]:
            print(f"  {label:16} snowflake={sfv:6}  python={py:6}  {'OK' if sfv == py else 'DIFF'}")


if __name__ == "__main__":
    main()
