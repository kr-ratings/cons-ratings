"""공사비 지표 수집

1) 건설공사비지수(2020=100) — 한국건설기술연구원(cost.kict.re.kr) 게시판의 최신 월 엑셀.
   매월 말 전전월 지수가 올라오며, 엑셀 한 개에 2000년 1월부터 전 기간이 들어 있어 과거 수정치도 함께 반영된다.
"""
import io, re, datetime as dt

import openpyxl, requests

KICT = "https://cost.kict.re.kr"
SINCE = "2015-01"  # 화면 기간 선택(최대 10년)보다 넉넉하게
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


SOURCES = {"cci": kict_cci}
LABEL = {"cci": "공사비지수"}
