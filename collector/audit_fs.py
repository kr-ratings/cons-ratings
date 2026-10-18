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


def _nodes(rcept_no):
    html = _get(f"{VIEW}/dsaf001/main.do", params={"rcpNo": rcept_no}).text
    nodes = []
    for m in re.finditer(r"node\d+\['text'\]\s*=\s*\"(.*?)\";(.*?)(?=node\d+\['text'\]|cnt\+\+|$)", html, re.S):
        body = m.group(2)
        g = lambda k: (re.search(rf"\['{k}'\]\s*=\s*\"(.*?)\"", body) or [None, None])[1]
        nodes.append({"text": _sp(m.group(1)), "dcmNo": g("dcmNo"), "eleId": g("eleId"), "offset": g("offset"), "length": g("length"), "dtd": g("dtd")})
    return nodes


SKIP_NM = re.compile(r"^(합계|합 계|계|총계|총 계|소계|보통주|우선주|보퉁주|종류주|주주명|성명|구분|x|X)$")


NUMLIKE = re.compile(r"^[\d,.\s]+(주|원|천원|백만원|%)?$")


def _holders_from_table(t):
    rows = t.find_all("tr")
    if len(rows) < 2 or len(rows) > 60:
        return None
    head = " ".join(_sp(c.get_text(" ")) for tr in rows[:2] for c in tr.find_all(["th", "td"]))
    if not (re.search(r"주주|성명", head) and re.search(r"지분|비율|%", head)):
        return None
    if re.search(r"종속|관계기업|회사명|투자|보증", head) and not re.search(r"주주", head):
        return None
    out = []
    for tr in rows:
        cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        names = [(i, c) for i, c in enumerate(cells) if c and not NUMLIKE.match(c) and not SKIP_NM.match(_sp(c)) and c != '"']
        if not names:
            continue
        ni, nm = names[0]
        if re.search(r"합계|총계|소계|^계$", _sp(nm)):
            continue
        # 이름 다음에서 처음 나오는 지분율(100 이하, % 또는 소수점) = 당기말 지분율
        pct = None
        for c in cells[ni + 1:]:
            v = _num(c.replace("%", "").replace("주", ""))
            if v is not None and 0 <= v <= 100 and ("%" in c or "." in c or v == 100) and "주" not in c:
                pct = v
                break
        if pct is None:
            continue
        rel = next((c for c in reversed(cells[ni + 1:]) if c and not NUMLIKE.match(c) and c not in ("-", '"') and not SKIP_NM.match(_sp(c))), None)
        out.append({"nm": nm, "pct": round(pct, 2), **({"rel": rel} if rel and len(rel) <= 12 else {})})
    tot = sum(x["pct"] for x in out)
    if out and 95 <= tot <= 100.5:
        return out
    return None


TITLE = r"(?:\s?(?:회장|의장|대표이사|대표|사장|님))?"
NAME = r"((?:\(주\)|㈜|\(재\))?[가-힣A-Za-z][가-힣A-Za-z0-9&]*(?:\(주\)|㈜|\(유\))?" + TITLE + r")"
PAIR_RX = re.compile(NAME + r"\s*[\(（]\s*(?:지분율\s*)?([\d.]+)\s*%\s*[\)）]")
TEXT_PATTERNS = [
    (re.compile(r"([\d.]+)\s*%\s*(?:의\s*지분)?(?:을|를)?\s*(?:보유|소유|가지고)(?:하고)?\s*있는\s*" + NAME), (2, 1)),
    (re.compile(NAME + r"(?:이|가)\s*[\d,]+\s*주\s*\(\s*지분율\s*([\d.]+)\s*%\s*\)"), (1, 2)),
    (re.compile(r"대주주\s*([가-힣]+)\s*및\s*특수관계(?:자|인)의\s*지분율은\s*([\d.]+)\s*%"), (1, 2)),
    (re.compile(r"(대주주\s*(?:와\s*그|및)\s*(?:그\s*)?특수관계(?:자|인))(?:이|가|의)?\s*(?:소유의\s*회사)?\s*(?:지분율은\s*)?([\d.]+)\s*%"), (1, 2)),
]
DROP = re.compile(r"종속|관계기업|투자|피투자|취득|매각|양수|양도|출자|지분법")


def _clean_nm(n):
    n = re.sub(r"^(최대주주는|주요\s*주주는|주주는|대주주인|대주주|최대주주인)\s*", "", n.strip())
    n = re.sub(r"(입니다|이며|이고|이다)$", "", n)
    return re.sub(r"\s?(회장|의장|대표이사|대표|사장|님)$", "", n).strip()


def _holders_from_text(text):
    """주석 1(회사의 개요) 문장에서 주주와 지분율을 읽는다"""
    sents = re.split(r"(?<=다)\.\s|\.\s(?=\(?\d)", text[:6000])
    for sent in sents:
        if not re.search(r"주주|지분", sent) or DROP.search(sent):
            continue
        if re.search(r"주주|지분율", sent):
            pairs = [(_clean_nm(m.group(1)), float(m.group(2))) for m in PAIR_RX.finditer(sent)]
            pairs = [(n, v) for n, v in pairs if len(n) >= 2 and 0 < v <= 100 and n not in ("지분율",)]
            if pairs:
                return [{"nm": n, "pct": round(v, 2)} for n, v in pairs]
        for rx, (ni, vi) in TEXT_PATTERNS:
            m = rx.search(sent)
            if m:
                v = float(m.group(vi))
                if 0 < v <= 100:
                    nm = _clean_nm(m.group(ni))
                    if "특수관계" in m.group(0):
                        nm = "대주주 및 특수관계인" if (not nm or "특수관계" in nm) else f"{nm} 및 특수관계인"
                    return [{"nm": nm, "pct": round(v, 2)}]
        m = re.search(r"지분은\s*(?:대표이사\s*)?([가-힣]+\s*외\s*\d+\s*인)(?:으로|로)\s*구성", sent)
        if m:
            return [{"nm": re.sub(r"\s*외\s*", " 외 ", m.group(1)), "pct": 100.0}]
    return None


def shareholders(rcept_no):
    """감사보고서 주석의 주주 현황 표(또는 '주주는 ○○(지분율 100%)' 문장) → [{"nm", "pct"}] 또는 None"""
    nodes = _nodes(rcept_no)
    cands = [n for n in nodes if n["text"] == "주석" and n["dcmNo"]] or [n for n in nodes if "재무제표" in n["text"] and "첨부" in n["text"]]
    for node in cands:
        r = _get(f"{VIEW}/report/viewer.do", params={"rcpNo": rcept_no, "dcmNo": node["dcmNo"], "eleId": node["eleId"],
                                                      "offset": node["offset"], "length": node["length"], "dtd": node["dtd"]})
        soup = BeautifulSoup(r.content, "html.parser")
        for t in soup.find_all("table"):
            h = _holders_from_table(t)
            if h:
                return sorted(h, key=lambda x: -x["pct"])
        text = re.sub(r"\s+", " ", soup.get_text(" "))
        h = _holders_from_text(text)
        if h:
            return h
    return None


def _norm_co(n):
    return re.sub(r"\s|\(주\)|㈜|주식회사|\(|\)", "", n or "")


def group_holders(rcept_no, names):
    """대규모기업집단현황공시 '소유지분현황' 표에서 이 회사 블록의 주주 → [{"nm", "rel", "pct"}] 또는 None"""
    nodes = _nodes(rcept_no)
    node = next((n for n in nodes if "소유지분현황" in n["text"] and n["dcmNo"]), None)
    if not node:
        return None
    r = _get(f"{VIEW}/report/viewer.do", params={"rcpNo": rcept_no, "dcmNo": node["dcmNo"], "eleId": node["eleId"],
                                                  "offset": node["offset"], "length": node["length"], "dtd": node["dtd"]})
    soup = BeautifulSoup(r.content, "html.parser")
    keys = {_norm_co(n) for n in names if n}
    for t in soup.find_all("table"):
        cur, out = None, []
        for tr in t.find_all("tr"):
            cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            hit = next((c for c in cells[:3] if _norm_co(c) in keys), None)
            if hit and _norm_co(hit) in keys and (cur is None or cells.index(hit) < 2):
                if cur and out:
                    break
                cur = hit
            elif cur and any(_norm_co(c) not in keys and re.search(r"\(주\)|㈜", c) for c in cells[:1]) and "동일인측" in " ".join(cells[:3]):
                break  # 다음 회사 블록
            if not cur:
                continue
            j = cells.index(hit) + 1 if hit in cells else 0
            if any(re.search(r"합계|총 ?계", c) for c in cells):
                if re.search(r"총 ?계", " ".join(cells)) and out:
                    break
                continue
            nums = [(i, _num(c.replace("%", ""))) for i, c in enumerate(cells)]
            pcts = [(i, v) for i, v in nums if v is not None and "%" in cells[i]]
            if not pcts:
                continue
            first_num = next(i for i, v in nums if v is not None)
            label = [c for c in cells[j:first_num] if c and c != "X"]
            if not label:
                continue
            out.append({"nm": label[-1], "rel": label[-2] if len(label) >= 2 else None, "pct": round(pcts[-1][1], 2)})
        if out:
            tot = sum(x["pct"] for x in out)
            if 50 <= tot <= 100.5:
                return sorted(out, key=lambda x: -x["pct"])
    return None
