"""감사보고서(외부감사 대상 비상장 법인) 원문에서 재무 요약 읽기 — DART 웹 공시뷰어(키 불필요)

사업·분기보고서를 내지 않는 회사는 Open DART 재무 API에 숫자가 없지만, 매년 제출하는 감사보고서 본문에
재무상태표 · 손익계산서가 들어 있다. 공시뷰어에서 '(첨부)재무제표' 부분을 받아 표에서
자산·부채·자본총계, 매출액, 영업이익, 당기순이익의 당기·전기 금액을 읽는다.
"""
import re, time

VERSION = 2  # 파서가 바뀌면 올린다 → 저장해 둔 읽은 결과를 다시 읽음

import requests
from bs4 import BeautifulSoup

VIEW = "https://dart.fss.or.kr"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _get(url, **kw):
    for i in range(4):
        try:
            r = S.get(url, timeout=60, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException:
            if i == 3:
                raise
            time.sleep(3 * (i + 1))


def _sp(s):
    return re.sub(r"[\s　\xa0]+", "", s or "")


def _num(s):
    t = _sp(s).replace(",", "")
    if not t or t in ("-", "−"):
        return None
    neg = t.startswith("(") and t.endswith(")") or t[0] in "△▲-−"
    t = t.strip("()△▲-−")
    if not re.fullmatch(r"\d+(\.\d+)?", t):
        return None
    v = float(t)
    return -v if neg else v


def _label(s):
    l = _sp(s)
    l = re.sub(r"^([IVXⅠ-Ⅻ]+|[0-9]+|[가-하])[\.\)]", "", l)  # 'Ⅰ.' '1.' '가.' 번호 제거
    l = re.sub(r"[\(<]주[석\d,\.\s]*[\)>]", "", l)
    return l


KEYS = [  # (키, 정규식) — 표에서 처음 맞는 행을 쓴다
    ("assets", r"^자산총계"),
    ("liab", r"^부채총계"),
    ("equity", r"^자본총계"),
    ("rev", r"^(매출액|매출|영업수익|수익\(매출액\)|매출및영업수익|공사수익)$|^매출액\("),
    ("op", r"^(영업이익|영업손실|영업손익|영업이익\(손실\)|영업손실\(이익\))"),
    ("ni", r"^(연결)?(당기순이익|당기순손실|당기순손익|당기순이익\(손실\)|당기순손실\(이익\))"),
]


def _unit(text):
    t = _sp(text)
    if "백만원" in t:
        return 1e2   # 백만원 → 억원: /100
    if "천원" in t:
        return 1e5
    return 1e8


PERIOD_RX = re.compile(r"제\s*\d+\s*(\(.*?\))?\s*기|당기|전기|\d{4}")


def _statement(t):
    """본문 표 하나 → {항목: [기간1, 기간2, ...]} (머리글 순서 = 최근 기간부터)"""
    out = {}
    rows = t.find_all("tr")
    if not rows:
        return out
    head = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
    has_note = any("주석" in _sp(h) for h in head)
    np_ = sum(1 for h in head[1:] if PERIOD_RX.search(_sp(h)) and "주석" not in _sp(h)) or 2
    for tr in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        lab = _label(cells[0])
        vals = cells[2:] if has_note and len(cells) >= np_ + 2 else cells[1:]
        nums = [_num(v) for v in vals]
        if not any(n is not None for n in nums):
            continue
        size = max(1, len(nums) // np_)
        per = []
        for i in range(np_):
            g = [n for n in nums[i * size:(i + 1) * size if i < np_ - 1 else None] if n is not None]
            per.append(g[-1] if g else None)
        for k, rx in KEYS:
            if k not in out and re.search(rx, lab):
                if k in ("op", "ni") and "손실" in lab and "이익" not in lab:  # '영업손실' 행은 양수로 적힌다
                    per = [-abs(v) if v is not None else None for v in per]
                out[k] = per
    return out


def _unit_of(t):
    s = t.find_previous(string=re.compile(r"단\s*위"))
    return _unit(s) if s else 1e8


def _read(rcept_no, node):
    r = _get(f"{VIEW}/report/viewer.do", params={"rcpNo": rcept_no, "dcmNo": node["dcmNo"], "eleId": node["eleId"],
                                                  "offset": node["offset"], "length": node["length"], "dtd": node["dtd"]})
    soup = BeautifulSoup(r.content, "html.parser")
    vals, got_is = {}, False
    for t in soup.find_all("table"):
        if len(t.find_all("tr")) < 8:
            continue
        st = _statement(t)
        if "assets" not in vals and "assets" in st:
            u = _unit_of(t)
            for k in ("assets", "liab", "equity"):
                if k in st:
                    vals[k] = [None if v is None else round(v / u) for v in st[k]]
        elif not got_is and "assets" in vals and ("rev" in st or "op" in st):
            u = _unit_of(t)
            for k in ("rev", "op", "ni"):
                if k in st:
                    vals[k] = [None if v is None else round(v / u) for v in st[k]]
            got_is = True
        if got_is:
            break
    return vals


NODE_PREF = [r"^\(첨부\)연결재무제표", r"^\(첨부\)재무제표", r"^\d+\.연결재무제표$", r"^\d+\.재무제표$"]


def parse(rcept_no, prefer_cons=True):
    """감사보고서 · 사업보고서 원문 → {"periods": [{항목: 억원}, ...최근부터], "consolidated": bool} 또는 None"""
    html = _get(f"{VIEW}/dsaf001/main.do", params={"rcpNo": rcept_no}).text
    nodes = []
    for m in re.finditer(r"node\d+\['text'\]\s*=\s*\"(.*?)\";(.*?)(?=node\d+\['text'\]|cnt\+\+|$)", html, re.S):
        body = m.group(2)
        g = lambda k: (re.search(rf"\['{k}'\]\s*=\s*\"(.*?)\"", body) or [None, None])[1]
        nodes.append({"text": _sp(m.group(1)), "dcmNo": g("dcmNo"), "eleId": g("eleId"), "offset": g("offset"), "length": g("length"), "dtd": g("dtd")})
    order = NODE_PREF if prefer_cons else [NODE_PREF[1], NODE_PREF[0], NODE_PREF[3], NODE_PREF[2]]
    cands = [n for rx in order for n in nodes if re.search(rx, n["text"]) and n["dcmNo"]]
    for node in cands:  # '해당 사항 없음'(연결 대상 없음)이면 다음 후보(별도 재무제표)로
        vals = _read(rcept_no, node)
        if "assets" in vals:
            break
    else:
        return None
    n = max(len(v) for v in vals.values())
    periods = [{k: (v[i] if i < len(v) else None) for k, v in vals.items()} for i in range(n)]
    return {"v": VERSION, "periods": periods, "consolidated": "연결" in node["text"]}
