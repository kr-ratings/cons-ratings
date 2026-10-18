#!/usr/bin/env python3
"""시공능력평가 · 신용등급 현황 수집기

1) 대한건설협회 공시자료 게시판에서 최신 연도와 직전 연도 '종합건설사업자 시공능력평가액 공시' 엑셀을 받아
   <토목건축공사업>(토건) 시트 상위 100개사 순위·평가액을 읽는다. (매년 7월 말 공시, 정정 공시도 자동 반영)
2) collector/agency_ids.json 의 평가사 기업코드로 한국신용평가(KIS)·한국기업평가(KR)·나이스신용평가(NICE)
   기업 페이지를 열어 현재 등급 · Outlook · 평가일을 읽는다. 대표 등급은 회사채(선순위)이고,
   회사채 등급이 없으면 기업신용등급(ICR), 그것도 없으면 기업어음(CP) 등급을 종류와 함께 남긴다.
3) data/contractors.json, data/status.json 을 쓴다. 한 평가사 수집이 실패하면 직전 값을 유지하고 상태에 표시한다.

사용: python collector/collect.py            (저장소 루트에서 실행)
"""
import io, json, re, sys, time, datetime as dt
from pathlib import Path

import requests, openpyxl
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
IDS_PATH = ROOT / "collector" / "agency_ids.json"
KST = dt.timezone(dt.timedelta(hours=9))
TOP_N = 100

CAK = "https://www.cak.or.kr"
CAK_BOARD = CAK + "/lay1/bbs/S1T10C14/A/4/list.do"
URL = {
    "KIS": "https://www.kisrating.com/ratingsSearch/corp_overview.do?kiscd={}",
    "KR": "http://www.korearatings.com/cms/frDisclosureCon/compView.do?MENU_ID=90&CONTENTS_NO=1&COMP_CD={}",
    "NICE": "https://www.nicerating.com/disclosure/companyGradeInfo.do?cmpCd={}",
}

S = requests.Session()
S.headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def get(url, tries=3, **kw):
    for i in range(tries):
        try:
            r = S.get(url, timeout=40, **kw)
            r.raise_for_status()
            return r
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def norm(name):
    """회사명 비교용 키: 법인격 표기와 공백 제거"""
    return re.sub(r"\s|\(주\)|㈜|주식회사|\(|\)", "", str(name or ""))


def display(name):
    """'삼성물산 주식회사' → '삼성물산㈜', '주식회사 플랜텍' → '㈜플랜텍', '(주)대우건설' → '㈜대우건설'"""
    n = re.sub(r"\s+", " ", str(name).strip())
    if n.startswith("주식회사"):
        n = "㈜" + n[4:].strip()
    elif n.endswith("주식회사"):
        n = n[:-4].strip() + "㈜"
    return re.sub(r"\s*\(주\)\s*", "㈜", n)


def ymd(s):
    s = (s or "").strip().replace(".", "-")
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None


# ---------------------------------------------------------------- 시공능력평가
def cak_posts():
    """게시판 목록 → {연도: view URL} (같은 연도는 최신 글 우선)"""
    soup = BeautifulSoup(get(CAK_BOARD).text, "html.parser")
    posts = {}
    for tr in soup.select("table tr"):
        a = tr.find("a", href=re.compile(r"view\.do"))
        if not a:
            continue
        m = re.search(r"(\d{4})년도\s*종합건설\S*\s*시공능력평가액\s*공시", a.get_text(" ", strip=True))
        if m and int(m.group(1)) not in posts:
            posts[int(m.group(1))] = requests.compat.urljoin(CAK_BOARD, a["href"])
    return posts


def cak_xlsx(view_url):
    soup = BeautifulSoup(get(view_url).text, "html.parser")
    a = soup.find("a", href=re.compile(r"download\.do\?uuid=.+\.xlsx"))
    if not a:
        raise RuntimeError("첨부 엑셀을 찾지 못함: " + view_url)
    url = requests.compat.urljoin(CAK, a["href"])
    return url, get(url).content


def parse_sipyeong(content, limit=None):
    """토건 시트 → [{rank, name, reg, region, amount(억원), parts…}]"""
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = next((w for w in wb.worksheets if "토건" in w.title), wb.worksheets[0])
    out = []
    for row in ws.iter_rows(values_only=True):
        if not row or not isinstance(row[0], (int, float)) or not row[1]:
            continue
        mil = lambda v: round(float(v) / 100, 1) if isinstance(v, (int, float)) else None  # 백만원 → 억원
        out.append({
            "rank": int(row[0]), "name": display(row[1]), "reg": str(row[5] or "").strip(),
            "region": (row[3] or "").strip() or None, "amount": mil(row[6]),
            "parts": {"실적": mil(row[7]), "경영": mil(row[8]), "기술": mil(row[9]), "신인도": mil(row[10])},
            "civil": mil(row[11]) if len(row) > 11 else None, "arch": mil(row[12]) if len(row) > 12 else None,
        })
        if limit and len(out) >= limit:
            break
    if len(out) < 50:
        raise RuntimeError(f"시평 엑셀 파싱 결과가 너무 적음({len(out)}행) — 양식 변경 확인 필요")
    return out


# ---------------------------------------------------------------- 신용평가 3사
# 각 함수는 {"bond": 회사채(선순위), "icr": 기업신용등급, "cp": 기업어음·전자단기사채} 중 있는 것만 돌려준다.
def _kind(label):
    l = label.replace(" ", "")
    if l.startswith("회사채(선순위)"):
        return "bond"
    if l.startswith("IssuerRating") or l.startswith("ICR") or "기업신용" in l:
        return "icr"
    if l.startswith("기업어음"):
        return "cp"
    if l.startswith("전자단기사채") or l.startswith("단기사채"):
        return "stb"
    return None


def _finish(found):
    if "cp" not in found and "stb" in found:
        found["cp"] = found["stb"]
    found.pop("stb", None)
    return {k: v for k, v in found.items() if v.get("grade") and v["grade"] != "-"}


def kis(code):
    soup = BeautifulSoup(get(URL["KIS"].format(code)).text, "html.parser")
    found = {}
    for dl in soup.select("div.list dl"):
        dt_ = dl.find("dt")
        k = _kind(dt_.get_text(strip=True)) if dt_ else None
        if not k or k in found:
            continue
        g = dl.find("strong")
        ent = {"grade": g.get_text(strip=True) if g else None}
        for dd in dl.find_all("dd"):
            sp = dd.find("span")
            lab = sp.get_text(strip=True) if sp else ""
            val = dd.get_text(" ", strip=True)[len(lab):].strip()
            if lab.startswith("Outlook") or lab == "Watchlist":
                ent["outlook"] = val or None
            elif lab == "평가일":
                ent["date"] = ymd(val)
        found[k] = ent
    return _finish(found), []


def kr(code):
    html = get(URL["KR"].format(code)).text
    m = re.search(r"mySheet9\.LoadSearchData\('(\{\"Data\":.*?\})'\)", html)  # 주요 등급
    rows = json.loads(m.group(1))["Data"] if m else []
    hist = []
    m1 = re.search(r"mySheet1\.LoadSearchData\('(\{\"Data\":.*?\})'\)", html)  # 회사채 회차별 등급 이력
    for b in (json.loads(m1.group(1))["Data"] if m1 else []):
        if b.get("GUAR_DVCD") not in (None, "0") or not b.get("GRD"):
            continue  # 보증 회사채 제외
        hist.append([ymd(b.get("EVAL_DT")), b["GRD"], b.get("OL_NM"), "bond", b.get("EVAL_DIV_NM")])
    found, keys = {}, {}
    for r in rows:
        k = _kind(r.get("EVAL_TRGT_NM_ORG") or r.get("EVAL_TRGT_NM") or "")
        if not k or not r.get("GRD") or r.get("GRD_VALD_YN") not in (None, "Y"):
            continue  # 유효기간이 끝난(만기 상환 등) 등급은 제외
        d = ymd(r.get("EVAL_DT"))
        key = (r.get("GUAR_DVCD") in (None, "0"), d or "")  # 무보증 우선, 그다음 최신
        if k not in found or key > keys[k]:
            found[k], keys[k] = {"grade": r["GRD"], "outlook": r.get("OL_NM"), "date": d}, key
    return _finish(found), _dedupe(hist)


NICE_SEC = {"채권": "bond", "기업신용평가": "icr", "기업어음": "cp"}


def _nice_hist(soup):
    """NICE 기업 페이지의 '채권 · 기업어음 · 기업신용평가' 목록 표 → [[평가일, 등급, Outlook, 종류, 평정]]"""
    hist = []
    for t in soup.find_all("table"):
        cap = t.find("caption")
        if not cap or "목록" not in cap.get_text():
            continue
        s, label = t, None
        for _ in range(6):
            s = s.find_previous(string=True)
            while s is not None and not s.strip():
                s = s.find_previous(string=True)
            if s is not None and s.strip() in NICE_SEC:
                label = NICE_SEC[s.strip()]
                break
        if not label:
            continue
        for tr in t.find_all("tr"):
            c = [x.get_text(" ", strip=True) for x in tr.find_all("td")]
            if label == "bond" and len(c) >= 7 and c[1].startswith("선순위"):
                hist.append([ymd(c[6]), c[4], c[5] or None, "bond", c[3]])
            elif label == "icr" and len(c) >= 4 and ymd(c[3]):
                hist.append([ymd(c[3]), c[1], c[2] or None, "icr", c[0]])
            elif label == "cp" and len(c) >= 3 and ymd(c[2]):
                hist.append([ymd(c[2]), c[1], None, "cp", c[0]])
    return hist


def _dedupe(hist):
    """같은 날 같은 종류는 하나만, 최신순, 종류별 최근 15건"""
    seen, out = set(), []
    for h in sorted((h for h in hist if h[0] and h[1]), key=lambda h: h[0], reverse=True):
        k = (h[0], h[3])
        if k in seen:
            continue
        seen.add(k)
        out.append(h)
    by = {}
    for h in out:
        by.setdefault(h[3], []).append(h)
    return [h for k in ("bond", "icr", "cp") for h in by.get(k, [])[:15]]


def nice(code):
    soup = BeautifulSoup(get(URL["NICE"].format(code)).text, "html.parser")
    found = {}
    for t in soup.find_all("table"):
        cap = t.find("caption")
        if not cap or "주요 등급내역" not in cap.get_text():
            continue
        for tr in t.find_all("tr"):
            c = [x.get_text(" ", strip=True) for x in tr.find_all("td")]
            if len(c) < 9:
                continue
            k = _kind(c[0])
            if k and k not in found:
                found[k] = {"grade": c[6], "outlook": c[7] or None, "date": ymd(c[8])}
        break
    return _finish(found), _dedupe(_nice_hist(soup))


SCRAPE = {"KIS": kis, "KR": kr, "NICE": nice}


# ---------------------------------------------------------------- 변동 기록 · 기사 · 공사비 · DART
def _load(name, default):
    p = DATA / name
    try:
        return json.loads(p.read_text("utf-8")) if p.exists() else default
    except Exception:
        return default


def _save(name, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), "utf-8")


def extras(prev, cur, status, now, failed):
    today = now.strftime("%Y-%m-%d")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import events, news, cost, dart

    # 1) 등급 · Outlook · 시평 변동 (직전 수집과 비교)
    try:
        ev = events.diff(prev, cur, today, failed) if prev.get("rows") else []
        old = _load("events.json", {}).get("events", [])
        _save("events.json", {"generated": now.strftime("%Y-%m-%d %H:%M"), "events": events.merge(old, ev, today)})
        print(f"변동 {len(ev)}건")
    except Exception as e:
        print("변동 기록 실패:", e, file=sys.stderr)

    # 2) 기사
    try:
        old = _load("news.json", {}).get("items", [])
        items, nf = news.collect(cur["rows"], old, get, log=lambda m: print(m, file=sys.stderr))
        _save("news.json", {"generated": now.strftime("%Y-%m-%d %H:%M"), "items": items})
        status["sources"]["기사"] = {"ok": nf == 0, **({"error": f"검색 {nf}건 실패"} if nf else {})}
        print(f"기사 {len(items)}건")
    except Exception as e:
        status["sources"]["기사"] = {"ok": False, "error": str(e)[:200]}
        print("기사 수집 실패:", e, file=sys.stderr)

    # 3) 공사비 지표
    c = _load("cost.json", {})
    for k, fn in cost.SOURCES.items():
        try:
            c[k] = fn()
            status["sources"][cost.LABEL[k]] = {"ok": True}
        except Exception as e:
            status["sources"][cost.LABEL[k]] = {"ok": False, "error": str(e)[:200]}
            print(f"{k} 수집 실패(직전 값 유지):", e, file=sys.stderr)
    c["generated"] = now.strftime("%Y-%m-%d %H:%M")
    _save("cost.json", c)

    # 4) DART 공시 · 재무 (DART_API_KEY 가 있을 때만)
    try:
        res = dart.collect(cur["rows"], _load("dart.json", {}), now, log=lambda m: print(m, file=sys.stderr))
        if res is not None:
            _save("dart.json", res)
            status["sources"]["DART"] = {"ok": not res.get("errors"), **({"error": "; ".join(res["errors"])[:200]} if res.get("errors") else {})}
    except Exception as e:
        status["sources"]["DART"] = {"ok": False, "error": str(e)[:200]}
        print("DART 수집 실패:", e, file=sys.stderr)


# ---------------------------------------------------------------- main
def main():
    now = dt.datetime.now(KST)
    DATA.mkdir(exist_ok=True)
    prev_path = DATA / "contractors.json"
    prev = json.loads(prev_path.read_text("utf-8")) if prev_path.exists() else {}
    prev_rows = {norm(r["name"]): r for r in prev.get("rows", [])}
    ids = json.loads(IDS_PATH.read_text("utf-8"))
    status = {"generated": now.strftime("%Y-%m-%d %H:%M"), "sources": {}}

    # 1) 시공능력평가
    try:
        posts = cak_posts()
        year = max(posts)
        src_url, content = cak_xlsx(posts[year])
        cur = parse_sipyeong(content, TOP_N)
        by_reg, by_name = {}, {}
        if year - 1 in posts:
            _, c2 = cak_xlsx(posts[year - 1])
            for r in parse_sipyeong(c2):
                if r["reg"]:
                    by_reg.setdefault(r["reg"], r["rank"])
                by_name.setdefault(norm(r["name"]), r["rank"])
        for r in cur:  # 등록번호 우선(사명 변경 대응), 없으면 회사명
            r["prev"] = by_reg.get(r["reg"]) or by_name.get(norm(r["name"]))
        status["sources"]["대한건설협회"] = {"ok": True, "year": year, "file": src_url}
    except Exception as e:
        if not prev.get("rows"):
            raise
        print("시평 수집 실패, 직전 데이터 유지:", e, file=sys.stderr)
        year = prev["year"]
        cur = [{k: v for k, v in r.items() if k != "ratings"} for r in prev["rows"]]
        status["sources"]["대한건설협회"] = {"ok": False, "error": str(e)[:200]}

    # 2) 신용평가 3사
    fail = {a: 0 for a in SCRAPE}
    unmapped = []
    hist_path = DATA / "history.json"
    H_old = json.loads(hist_path.read_text("utf-8")) if hist_path.exists() else {}
    H = {}
    ids_by_reg = {v["reg"]: v for v in ids.values() if v.get("reg")}
    for r in cur:
        key = norm(r["name"])
        codes = ids_by_reg.get(r.get("reg")) or ids.get(key)
        if codes is None:
            unmapped.append(r["name"])
            codes = {}
        old = (prev_rows.get(key) or {}).get("ratings", {})
        r["ratings"] = {}
        for a, fn in SCRAPE.items():
            code = codes.get(a)
            if not code:
                continue
            ent = {"url": URL[a].format(code)}
            try:
                got, hist = fn(code)
                if a != "KIS":
                    H.setdefault(r.get("reg") or key, {})[a] = hist
                # 대표 등급: 회사채(선순위) → 기업신용등급(ICR) → 기업어음(CP) 순
                kind = next((k for k in ("bond", "icr", "cp") if k in got), None)
                if kind:
                    ent.update({"kind": kind, **{k: v for k, v in got[kind].items() if v}})
                if "cp" in got and kind != "cp":
                    ent["cp"] = got["cp"]["grade"]
            except Exception as e:
                fail[a] += 1
                print(f"{a} {r['name']} 실패: {e}", file=sys.stderr)
                o = old.get(a) or {}
                ent.update({k: o[k] for k in ("grade", "outlook", "date", "kind", "cp") if o.get(k)})
            r["ratings"][a] = ent
            time.sleep(0.4)
    for a in SCRAPE:
        status["sources"][a] = {"ok": fail[a] == 0, **({"error": f"{fail[a]}개사 수집 실패(직전 값 유지)"} if fail[a] else {})}
    if unmapped:
        status["unmapped"] = unmapped

    # 등급 이력: NICE·KR은 평가사 페이지의 이력 표, KIS는 매일 본 현재 등급을 쌓는다. 실패한 평가사는 직전 이력 유지
    for r in cur:
        k = r.get("reg") or norm(r["name"])
        h, old_h = H.setdefault(k, {}), H_old.get(k, {})
        for a in ("KR", "NICE"):
            if a not in h and old_h.get(a):
                h[a] = old_h[a]
        kis_h = [list(x) for x in old_h.get("KIS", [])]
        x = r["ratings"].get("KIS") or {}
        if x.get("grade") and x.get("date") and not any(e[0] == x["date"] and e[3] == (x.get("kind") or "bond") for e in kis_h):
            kis_h.insert(0, [x["date"], x["grade"], x.get("outlook"), x.get("kind") or "bond", None])
        if kis_h:
            h["KIS"] = sorted(kis_h, key=lambda e: e[0], reverse=True)[:15]
        H[k] = {a: v for a, v in h.items() if v}
    hist_path.write_text(json.dumps({k: v for k, v in H.items() if v}, ensure_ascii=False, separators=(",", ":")), "utf-8")

    dates = [x["date"] for r in cur for x in r["ratings"].values() if x.get("date")]
    out = {
        "year": year,
        "rating_latest": max(dates) if dates else None,
        "collected": now.strftime("%Y-%m-%d"),
        "rows": cur,
    }
    prev_path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), "utf-8")
    extras(prev, out, status, now, [a for a in SCRAPE if fail[a]])
    (DATA / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), "utf-8")
    print(f"{year}년 시평 {len(cur)}개사 · 등급 {sum(len([x for x in r['ratings'].values() if x.get('grade')]) for r in cur)}건 · "
          f"실패 {fail} · 미매핑 {len(unmapped)}")


if __name__ == "__main__":
    main()
