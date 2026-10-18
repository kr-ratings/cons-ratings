"""공사비 지표 수집

1) 건설공사비지수(2020=100) — 한국건설기술연구원(cost.kict.re.kr) 게시판의 최신 월 엑셀.
   매월 말 전전월 지수가 올라오며, 엑셀 한 개에 2000년 1월부터 전 기간이 들어 있어 과거 수정치도 함께 반영된다.
2) 주요 자재 생산자물가지수(2020=100) — 한국은행 ECOS: 철근및봉강 · 레미콘 · 시멘트.
   ECOS_API_KEY 가 있으면 2015년부터 전부, 없으면 공개 샘플 키로 최근 10개월만 받아 저장된 값에 덧붙인다.
3) 시중노임단가 — 대한건설협회 '건설업 임금실태조사 보고서'(연 2회, 1월·9월 적용) 첨부 hwpx 의 노임 표.
"""
import html, io, json, os, re, time, zipfile, datetime as dt
from pathlib import Path
import xml.etree.ElementTree as ET

import openpyxl, requests

KICT = "https://cost.kict.re.kr"
SINCE = "2015-01"
# 영문 엑셀의 계열명 → 화면 이름 (원본 철자 'Costruction' 그대로)
CCI_KEYS = {
    "Costruction": "건설 전체",
    "Building Construction": "건축",
    "Housing": "주거용 건물",
    "Non-Housing": "비주거용 건물",
    "Civil & Special Construction": "토목",
}


def kict_cci():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": KICT + "/"})
    s.get(KICT + "/", timeout=30)  # XSRF-TOKEN 쿠키
    s.headers["X-XSRF-TOKEN"] = s.cookies.get("XSRF-TOKEN", "")
    posts = s.post(KICT + "/notice/list", json={"sortData": "index"}, timeout=30).json()
    posts = [p for p in posts if p.get("file_id") and "건설공사비지수" in p.get("board_title", "")]
    post = max(posts, key=lambda p: p["reg_dt"])
    detail = s.post(KICT + "/notice/get", json={"board_id": post["board_id"]}, timeout=30).json()
    f = next(x for x in detail["fileList"] if x["file_nm"].endswith(".xlsx") and "Construction" in x["file_nm"])
    r = s.get(KICT + "/file/download", params={"fileId": f["file_id"], "fileSeq": f["file_seq"]}, timeout=60)
    r.raise_for_status()
    ws = openpyxl.load_workbook(io.BytesIO(r.content), data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    months = []
    for h in rows[0][4:]:
        m = re.match(r"(\d{4})\D(\d{1,2})", str(h))
        months.append(f"{m.group(1)}-{int(m.group(2)):02d}" if m else None)
    series = {}
    for row in rows[1:]:
        lvl = next((i for i in range(4) if row[i]), None)
        if lvl is None:
            continue
        name = str(row[lvl]).strip()
        if name in CCI_KEYS and CCI_KEYS[name] not in series:
            series[CCI_KEYS[name]] = [[m, round(float(v), 2)] for m, v in zip(months, row[4:])
                                      if m and m >= SINCE and isinstance(v, (int, float))]
    if "건설 전체" not in series or len(series["건설 전체"]) < 24:
        raise RuntimeError("건설공사비지수 엑셀 양식 변경 확인 필요")
    reg = str(post.get("reg_dt") or "")[:10]
    return {
        "title": post.get("board_title"),
        "posted": reg,
        "provisional": "Provisional" in str(rows[0][-1]) or "잠정" in str(rows[0][-1]),
        "url": KICT + "/#/notice/file/index",
        "series": series,
    }


# ---------------------------------------------------------------- 자재 생산자물가지수 (ECOS)
PPI = [("철근및봉강", "404Y014", "307121AA"), ("레미콘", "404Y014", "306231AA"), ("시멘트", "404Y014", "306221AA")]
ECOS = "https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/{n}/{stat}/M/{a}/{b}/{item}"
COST_PATH = Path(__file__).resolve().parent.parent / "data" / "cost.json"


def _ecos(stat, item, a, b, key, n):
    url = ECOS.format(key=key, n=n, stat=stat, a=a, b=b, item=item)
    for i in range(8):  # ECOS 서버가 연결을 자주 끊어서 재시도
        try:
            d = requests.get(url, timeout=30).json()
            break
        except (requests.RequestException, ValueError):
            time.sleep(2 * (i + 1))
    else:
        raise RuntimeError("ECOS 연결 실패")
    rows = (d.get("StatisticSearch") or {}).get("row") or []
    return [[f"{r['TIME'][:4]}-{r['TIME'][4:6]}", round(float(r["DATA_VALUE"]), 2)] for r in rows if r.get("DATA_VALUE")]


def ecos_ppi():
    key = os.environ.get("ECOS_API_KEY", "").strip()
    old = {}
    try:
        old = (json.loads(COST_PATH.read_text("utf-8")).get("ppi") or {}).get("series", {})
    except Exception:
        pass
    now = dt.date.today()
    end = f"{now.year}{now.month:02d}"
    series = {}
    for name, stat, item in PPI:
        if key:
            pts = _ecos(stat, item, SINCE.replace("-", ""), end, key, 1000)
        else:
            # 샘플 키는 한 번에 10행까지 → 저장된 값이 없으면 10개월씩 나눠 채우고, 있으면 최근 10개월만 갱신
            pts = {m: v for m, v in old.get(name, [])}
            y, mo = (now.year, now.month - 9) if pts else (int(SINCE[:4]), 1)
            while (y, mo) <= (now.year, now.month):
                while mo < 1:
                    mo += 12; y -= 1
                a = f"{y}{mo:02d}"
                mo2, y2 = mo + 9, y
                while mo2 > 12:
                    mo2 -= 12; y2 += 1
                for m, v in _ecos(stat, item, a, f"{y2}{mo2:02d}", "sample", 10):
                    pts[m] = v
                y, mo = y2, mo2 + 1
                if mo > 12:
                    mo, y = 1, y + 1
                time.sleep(0.3)
            pts = sorted(pts.items())
            pts = [[m, v] for m, v in pts]
        if len(pts) < 12:
            raise RuntimeError(f"{name} 시계열이 너무 짧음({len(pts)})")
        series[name] = pts
    return {"series": series, "url": "https://ecos.bok.or.kr/", "note": "한국은행 생산자물가지수(기본분류, 2020=100)"}


# ---------------------------------------------------------------- 시중노임단가 (대한건설협회)
CAK = "https://www.cak.or.kr"
WAGE_LIST = CAK + "/lay1/bbs/S1T41C42/A/14/list.do"
JOBS = {"1002": "보통인부", "1003": "특별인부", "1007": "형틀목공", "1008": "철근공"}


def _get(url, **kw):
    for i in range(6):
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"}, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if i == 5:
                raise
            time.sleep(2 * (i + 1))


def cak_wage():
    t = _get(WAGE_LIST).text
    for tr in re.findall(r"<tr>(.*?)</tr>", t, re.S):
        m = re.search(r'article_seq=(\d+)[^>]*>\s*(.*?)\s*</a>', tr, re.S)
        if not m or "임금실태조사 보고서" not in m.group(2) or "참고" in m.group(2):
            continue
        files = re.findall(r"fnFileView\('attach', '([^']+)'\)", tr)
        hw = next((f for f in files if f.endswith(".hwpx")), None)
        if not hw:
            continue
        seq, title = m.group(1), re.sub(r"<.*?>", "", html.unescape(m.group(2))).strip()
        break
    else:
        raise RuntimeError("임금실태조사 보고서(hwpx)를 찾지 못함")
    z = zipfile.ZipFile(io.BytesIO(_get(CAK + "/download.do", params={"uuid": hw}).content))
    header, out = None, {}
    for n in sorted(x for x in z.namelist() if re.match(r"Contents/section\d+\.xml$", x)):
        for el in ET.fromstring(z.read(n)).iter():
            if not el.tag.endswith("}tr"):
                continue
            cells = ["".join(x.text or "" for x in tc.iter() if x.tag.endswith("}t")).strip() for tc in el if tc.tag.endswith("}tc")]
            if len(cells) > 1 and cells[0].startswith("공") and re.match(r"\d{4}\.\d+\.\d+$", cells[1]):
                header = cells[1:]
            elif header and len(cells) == len(header) + 2 and cells[0] in JOBS:
                out[JOBS[cells[0]]] = [int(v.replace(",", "")) if v.replace(",", "").isdigit() else None for v in cells[2:]]
    if len(out) < 4:
        raise RuntimeError("노임단가 표 파싱 실패 — 양식 변경 확인 필요")
    iso = lambda s: "-".join(f"{int(x):02d}" for x in s.split("."))
    return {"title": title, "periods": [iso(h) for h in header], "wages": out,
            "url": f"{CAK}/lay1/bbs/S1T41C42/A/14/view.do?article_seq={seq}"}


SOURCES = {"cci": kict_cci, "ppi": ecos_ppi, "wage": cak_wage}
LABEL = {"cci": "공사비지수", "ppi": "자재 물가", "wage": "노임단가"}
