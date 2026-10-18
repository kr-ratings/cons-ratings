"""직전 수집 결과와 비교해 등급 · Outlook · 정기평가 · 시평 변동을 기록한다 (data/events.json)

등급 이력 원천은 이 파일 하나다. 매일 수집 때 생긴 변화만 쌓이므로, 이 기능을 켠 날 이후의 변동부터 남는다.
"""
import datetime as dt

KEEP_DAYS = 400
LT = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "CCC", "CC", "C", "D"]
ST = ["A1", "A2+", "A2", "A2-", "A3+", "A3", "A3-", "B+", "B", "B-", "C", "D"]
KIND = {"bond": "회사채", "icr": "ICR", "cp": "CP"}


def _rank(grade, kind):
    g = (grade or "").replace("↑", "").replace("↓", "").split("(")[0].strip()
    L = ST if kind == "cp" else LT
    return L.index(g) if g in L else None


def diff(prev, cur, today, failed_agencies=()):
    """prev/cur: contractors.json 형식. 반환: 새 이벤트 목록"""
    ev = []
    if prev.get("year") and cur.get("year") and cur["year"] != prev["year"]:
        ev.append({"d": today, "type": "sipyeong", "title": f"{cur['year']}년 시공능력평가 공시 반영",
                   "sub": f"상위 100개사 순위·평가액이 {cur['year']}년 기준으로 바뀌었습니다"})
    old = {r.get("reg") or r["name"]: r for r in prev.get("rows", [])}
    for r in cur.get("rows", []):
        o = old.get(r.get("reg") or r["name"])
        if not o:
            continue
        for a, x in (r.get("ratings") or {}).items():
            if a in failed_agencies:
                continue
            y = (o.get("ratings") or {}).get(a) or {}
            base = {"d": today, "reg": r.get("reg"), "co": r["name"], "ag": a, "url": x.get("url")}
            g1, g0 = x.get("grade"), y.get("grade")
            k1, k0 = x.get("kind") or "bond", y.get("kind") or "bond"
            if not g1 and not g0:
                continue
            if g1 and not g0:
                ev.append({**base, "type": "rating", "dir": "new", "kind": k1, "to": g1, "ol": x.get("outlook"), "ed": x.get("date"),
                           "title": f"{a} {KIND[k1]} {g1} 신규"})
                continue
            if g0 and not g1:
                ev.append({**base, "type": "rating", "dir": "off", "kind": k0, "from": g0, "ed": None,
                           "title": f"{a} {KIND[k0]} {g0} → 공시 등급 없음"})
                continue
            if k1 != k0:
                ev.append({**base, "type": "rating", "dir": "kind", "kind": k1, "from": f"{KIND[k0]} {g0}", "to": g1,
                           "ol": x.get("outlook"), "ed": x.get("date"), "title": f"{a} 대표 등급 {KIND[k0]} {g0} → {KIND[k1]} {g1}"})
                continue
            if g1 != g0:
                r1, r0 = _rank(g1, k1), _rank(g0, k0)
                d = "up" if (r1 is not None and r0 is not None and r1 < r0) else "down" if (r1 is not None and r0 is not None) else "chg"
                ev.append({**base, "type": "rating", "dir": d, "kind": k1, "from": g0, "to": g1, "ol": x.get("outlook"), "ed": x.get("date"),
                           "title": f"{a} {KIND[k1]} {g0} → {g1}"})
            elif (x.get("outlook") or "") != (y.get("outlook") or ""):
                ev.append({**base, "type": "outlook", "kind": k1, "grade": g1, "from": y.get("outlook") or "-", "to": x.get("outlook") or "-",
                           "ed": x.get("date"), "title": f"{a} {KIND[k1]} {g1} Outlook {y.get('outlook') or '-'} → {x.get('outlook') or '-'}"})
            elif x.get("date") and x.get("date") != y.get("date"):
                ev.append({**base, "type": "review", "kind": k1, "grade": g1, "ol": x.get("outlook"), "ed": x.get("date"),
                           "title": f"{a} {KIND[k1]} {g1} 유지"})
    return ev


def merge(old_events, new_events, today):
    cut = (dt.date.fromisoformat(today) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    seen, out = set(), []
    for e in [*new_events, *(old_events or [])]:
        k = (e["d"], e.get("type"), e.get("reg"), e.get("ag"), e.get("title"))
        if e["d"] < cut or k in seen:
            continue
        seen.add(k)
        out.append(e)
    out.sort(key=lambda e: e["d"], reverse=True)
    return out
