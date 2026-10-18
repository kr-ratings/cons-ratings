"""DART 공시·재무만 따로 수집 (Actions 'Run workflow'에서 only=dart 로 실행)"""
import json, sys, datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dart

DATA = Path(__file__).resolve().parent.parent / "data"
now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
rows = json.loads((DATA / "contractors.json").read_text("utf-8"))["rows"]
prev = json.loads((DATA / "dart.json").read_text("utf-8")) if (DATA / "dart.json").exists() else {}
res = dart.collect(rows, prev, now, log=lambda m: print(m, file=sys.stderr))
if res is None:
    sys.exit("DART_API_KEY 없음")
(DATA / "dart.json").write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")), "utf-8")
st = json.loads((DATA / "status.json").read_text("utf-8"))
st["sources"]["DART"] = {"ok": not res.get("errors"), **({"error": "; ".join(res["errors"])[:200]} if res.get("errors") else {})}
(DATA / "status.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), "utf-8")
ids = json.loads((Path(__file__).resolve().parent / "dart_ids.json").read_text("utf-8"))
print(f"매핑 {len(ids)}개사 · 재무 {sum(1 for v in res['co'].values() if v.get('fin'))}개사 · 오류 {res.get('errors')}")
for k, v in list(ids.items()):
    print(" ", v["name"], "→", v["dart_name"], v["corp_code"], v.get("stock") or "")
