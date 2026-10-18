"""상위 100개 시공사 관련 기사 수집 (구글 뉴스 RSS · 제목 · 출처 · 링크만)

- 회사별로 최근 3일 기사를 검색하고, 제목에 회사 이름이 있고 실무에 의미 있는 주제(리스크 · 신용·자금 · 실적 · 수주 · 지배구조)로
  분류되는 기사만 남긴다. 분양 홍보 · 사회공헌 같은 기사는 분류에 걸리지 않아 빠진다.
- 업계 전반 기사도 몇 가지 검색어로 따로 모은다(회사 이름이 나오면 그 회사에 붙인다).
- 같은 사건을 여러 매체가 쓴 기사는 하나로 묶고 건수(n)만 남긴다.
- 본문은 가져오지 않는다.
"""
import email.utils, hashlib, html, json, re, time, datetime as dt
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9))
NAMES_PATH = Path(__file__).resolve().parent / "news_names.json"
KEEP_DAYS = 30

CATS = [  # (분류, 제목 정규식) — 앞에 있는 분류가 우선
    ("리스크", r"워크아웃|법정관리|기업회생|회생절차|회생 신청|부도|유동성 (위기|우려|악화)|자금난|자금 압박|연체|영업정지|"
              r"중대재해|사망사고|붕괴|압수수색|과징금|기소|구속|공사대금 미지급|임금체불|부실|손상차손|채권단|"
              r"상장폐지|감사의견|한정의견|의견거절"),
    ("신용·자금", r"신용등급|등급 (상향|하향|전망)|등급전망|회사채|기업어음|단기사채|CP |차입|자금조달|유상증자|신종자본|영구채|"
                r"ABSTB|ABCP|PF|브릿지론|책임준공|신용보강|우발채무|보증|대출|유동화|자본확충|재무구조|부채비율|현금"),
    ("실적", r"실적|영업이익|영업손실|순이익|순손실|매출|적자|흑자|컨센서스|어닝|수익성|원가율"),
    ("수주", r"수주|시공사 선정|시공사로 선정|시공권|낙찰|우선협상|도급 계약|공급계약|본계약|따냈|확보"),
    ("지배구조", r"매각|인수|합병|M&A|최대주주|대표이사|신임 사장|사장 선임|CEO|지분|계열 분리|분할|희망퇴직|구조조정"),
]
CATS = [(c, re.compile(p)) for c, p in CATS]
INDUSTRY_Q = ["건설사 유동성", "건설사 신용등급", "건설사 PF 우발채무", "건설사 법정관리", "건설사 워크아웃", "건설 공사비 인상"]
INDUSTRY_MUST = re.compile(r"건설")
NOISE = re.compile(r"특징주|목표가|주가|\d+(\.\d+)?% (상승|하락|급등|급락)|강세|약세|\[포토\]|\[사진\]|인사\]|부고|모집|채용|캠페인|봉사|기부|후원")


def classify(title):
    for c, rx in CATS:
        if rx.search(title):
            return c
    return None


def _rss(get, q):
    r = get("https://news.google.com/rss/search", params={"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    out = []
    for it in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        g = lambda t: html.unescape((re.search(rf"<{t}[^>]*>(.*?)</{t}>", it, re.S) or [None, ""])[1]).strip()
        title, link, src = g("title"), g("link"), g("source")
        try:
            pub = email.utils.parsedate_to_datetime(g("pubDate")).astimezone(KST)
        except Exception:
            continue
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        title = re.sub(r"\s+-\s+[^-]{1,30}$", "", title).strip()
        out.append({"t": title, "src": src, "u": link, "d": pub.strftime("%Y-%m-%d %H:%M")})
    return out


def _key(t):
    return re.sub(r"[^\w가-힣]", "", re.sub(r"\[.*?\]|\(.*?\)|<.*?>", "", t))


def _similar(a, b):
    """제목 유사도(2글자 묶음 기준) — 같은 사건을 다른 매체가 쓴 기사 묶기용"""
    A = {a[i:i + 2] for i in range(len(a) - 1)}
    B = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(A & B) / max(1, min(len(A), len(B)))


def _h(u):
    return hashlib.md5(u.encode()).hexdigest()[:10]


def _cluster(items):
    """같은 회사(또는 업계) 안에서 비슷한 제목을 묶는다. 가장 이른 기사를 대표로 남기고,
    묶인 기사 링크의 해시를 h에 모은다(n = 묶인 건수). 다음 실행 때 같은 기사가 다시 와도 h로 걸러진다."""
    items = sorted(items, key=lambda x: x["d"])
    out = []
    for it in items:
        k = _key(it["t"])
        hs = it.get("h") or [_h(it["u"])]
        for o in out:
            if o.get("reg") == it.get("reg") and abs(_days(o["d"], it["d"])) <= 3 and (_key(o["t"]) == k or _similar(_key(o["t"]), k) >= 0.6):
                o["h"] = sorted(set(o["h"]) | set(hs))
                break
        else:
            out.append({**it, "h": hs})
    for o in out:
        o["n"] = len(o["h"])
    return out


def _days(a, b):
    f = lambda s: dt.datetime.strptime(s[:10], "%Y-%m-%d")
    return (f(a) - f(b)).days


def search_names(name):
    """회사명 → (검색어 목록, 제목 필수 표현 목록)"""
    names = json.loads(NAMES_PATH.read_text("utf-8"))
    base = re.sub(r"\s|\(주\)|㈜|주식회사", "", name)
    ov = names.get(base) or {}
    q = ov.get("q") or [base]
    must = ov.get("must") or q
    excl = re.compile(ov["exclude"]) if ov.get("exclude") else None
    return q, must, excl


def collect(rows, prev_items, get, log=print):
    """rows: 시평 상위 100개사, prev_items: 직전 news 목록 → 새 목록(최근 KEEP_DAYS일)"""
    now = dt.datetime.now(KST)
    cut = (now - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    got, fails = [], 0
    musts = []
    for r in rows:
        q, must, excl = search_names(r["name"])
        musts.append((r, must))
        for term in q:
            try:
                for it in _rss(get, f'"{term}" when:3d'):
                    if not any(m in it["t"] for m in must) or NOISE.search(it["t"]) or (excl and excl.search(it["t"])):
                        continue
                    c = classify(it["t"])
                    if c:
                        got.append({**it, "reg": r.get("reg"), "co": r["name"], "cat": c})
            except Exception as e:
                fails += 1
                log(f"기사 검색 실패 {term}: {e}")
            time.sleep(0.8)
    for term in INDUSTRY_Q:
        try:
            for it in _rss(get, f"{term} when:3d"):
                c = classify(it["t"])
                if not c or not INDUSTRY_MUST.search(it["t"]) or NOISE.search(it["t"]):
                    continue
                hit = next((r for r, must in musts if any(m in it["t"] for m in must)), None)
                got.append({**it, "reg": hit.get("reg") if hit else None, "co": hit["name"] if hit else None, "cat": c})
        except Exception as e:
            fails += 1
            log(f"업계 기사 검색 실패 {term}: {e}")
        time.sleep(0.8)
    # 직전 목록과 합치기: 이미 본 기사(링크 해시)는 다시 넣지 않는다
    seen, merged = set(), []
    for it in prev_items or []:
        if it["d"][:10] >= cut:
            merged.append(it)
            seen.update(it.get("h") or [_h(it["u"])])
    for it in got:
        h = _h(it["u"])
        if it["d"][:10] < cut or h in seen:
            continue
        seen.add(h)
        merged.append(it)
    out = _cluster(merged)
    out.sort(key=lambda x: x["d"], reverse=True)
    return out, fails
