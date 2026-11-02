"""수주 현황 · 보증·약정 현황 — DART 공시뷰어 원문 표 읽기(키 불필요)

1) 정기보고서(사업·반기·분기보고서)를 내는 회사
   - 수주: 'II. 사업의 내용 > 매출 및 수주상황'과 'XII. 상세표 > 수주상황(상세)'의 현장별 표를 모두 읽는다.
   - 보증·약정: 'XI. 그 밖에 투자자 보호를 위하여 필요한 사항 > 2. 우발부채 등에 관한 사항'에서
     부동산 PF 보증 세부 내역(현장별), 책임준공 · 중도금대출 · SOC 보증 요약을 읽는다.
2) 감사보고서만 내는 회사(비상장 외감법인)
   - 수주: 주석의 공사계약 잔액 변동 표(기초 · 신규 · 수익인식 · 기말).
   - 보증·약정: 주석 '우발부채와 약정사항'에서 남에게 제공한 보증 · 약정 표를 원문 그대로.
금액은 모두 억원으로 바꾼다. 소송 · 담보 · 제공받은 보증 표는 싣지 않는다.
"""
import re, time

from bs4 import BeautifulSoup

import audit_fs

VERSION = 2
_sp = audit_fs._sp


# ---------------------------------------------------------------- 공통: 원문 · 표
def node_html(rcept_no, node):
    r = audit_fs._get(f"{audit_fs.VIEW}/report/viewer.do",
                      params={"rcpNo": rcept_no, "dcmNo": node["dcmNo"], "eleId": node["eleId"],
                              "offset": node["offset"], "length": node["length"], "dtd": node["dtd"]})
    return r.content


def _txt(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def grid(t):
    """rowspan · colspan 을 풀어 2차원 표로"""
    G, carry = [], {}
    for tr in t.find_all("tr"):
        row, ci = [], 0
        cells = iter(tr.find_all(["td", "th"]))
        while True:
            while carry.get(ci, (0,))[0] > 0:
                n, v = carry[ci]
                row.append(v); carry[ci] = (n - 1, v); ci += 1
            c = next(cells, None)
            if c is None:
                break
            v = _txt(c)
            try:
                cs, rs = int(c.get("colspan", 1) or 1), int(c.get("rowspan", 1) or 1)
            except ValueError:
                cs, rs = 1, 1
            for _ in range(min(cs, 60)):
                row.append(v)
                if rs > 1:
                    carry[ci] = (rs - 1, v)
                ci += 1
        G.append(row)
    return G


def blocks(html):
    """문서 순서대로 ('p', 문장) · ('t', 표 격자) — 표 안의 표는 건너뛴다"""
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(["p", "table"]):
        if el.find_parent("table") is not None:
            continue
        if el.name == "p":
            t = _txt(el)
            if t:
                yield "p", t
        else:
            G = grid(el)
            if G:
                yield "t", G


UNIT_RX = re.compile(r"단\s*위\s*[:：]?\s*\(?\s*(?:[^()\]\[]{0,24}?[,·/]\s*)?(억\s*원|백\s*만\s*원|천\s*원|원)(?![가-힣])")
UNITS = {"억원": 1, "백만원": 1e2, "천원": 1e5, "원": 1e8}


def unit_of(s):
    m = None
    for m in UNIT_RX.finditer(s or ""):
        pass
    return UNITS[_sp(m.group(1))] if m else None


NUMC = re.compile(r"^\(?[-△▲]?\(?\d[\d,]*(\.\d+)?\)?$")


def num(s):
    s = re.sub(r"\(\*\d*\)|\(주\d*\)|\*+\d*", "", s or "")
    return audit_fs._num(s.replace("%", ""))


def is_num(s):
    s = _sp(re.sub(r"\(\*\d*\)|\(주\d*\)|\*+\d*", "", s or ""))
    return bool(NUMC.match(s)) and len(re.sub(r"\D", "", s)) >= 2


def split(G):
    """→ (열 이름 목록, 데이터 행들, 표 안 단위). 맨 위 단위 행 · 머리글 여러 줄을 처리"""
    i, unit = 0, None
    while i < len(G) and len({_sp(c) for c in G[i] if _sp(c)}) <= 1 and i < len(G) - 1:
        unit = unit or unit_of(" ".join(G[i]))
        i += 1
    h = i
    while h < len(G) and not any(is_num(c) for c in G[h][1:]):
        h += 1
    if h == i:  # 머리글 없는 표
        return [], G[i:], unit
    n = max(len(r) for r in G[i:])
    cols = []
    for j in range(n):
        parts = []
        for r in G[i:h]:
            v = _sp(r[j]) if j < len(r) else ""
            if v and v not in parts:
                parts.append(v)
        cols.append("/".join(parts))
    unit = unit or unit_of(" ".join(" ".join(r) for r in G[i:h]))
    return cols, G[h:], unit


def find_col(cols, rx, avoid=None, last=False):
    hits = [j for j, c in enumerate(cols) if re.search(rx, c) and not (avoid and re.search(avoid, c))]
    if not hits:
        return None
    return hits[-1] if last else hits[0]


TOTAL = re.compile(r"(합계|총계|소계|^계$|^합$|[가-힣\)]\s*계$|^\(?계\)?$)")
EOK = lambda v, u: None if v is None else round(v / u, 1)


# ---------------------------------------------------------------- 정기보고서: 수주
ORD_NODE = re.compile(r"매출및수주상황|수주.*상세|^\d+\.수주상황$|수주현황")
ROLE = [  # (역할, 열 이름 정규식, 피할 정규식)
    ("own", r"발주처|발주자|발주기관|고객|^발주$(?!x)", r"공종"),
    ("nm", r"공사명|현장명|프로젝트|계약명|용역명|사업명|품목|공사$|현장$", r"발주|공사건수|완성공사|착공|기한|시작|종료"),
    ("co", r"^회사|^사명|^지배회사", None),
    ("biz", r"^사업$|사업부문|^부문", None),
    ("cls", r"^구분|^공종|^발주$", None),
    ("cd", r"계약일|수주일|착공|공사시작|계약시작|계약년월|수주\s*일자|계약일자", None),
    ("ed", r"완공|준공|납기|완성기한|종료|공사기한|완료", None),
    ("amt", r"기본도급|도급액|도급금액|수주총액|계약금액|최종도급|수주금액|총도급", r"수량"),
    ("done", r"완성공사|기납품|기성|매출액|누계|누적", r"수량|잔"),
    ("rem", r"계약잔액|수주잔|잔액|잔고|진행잔액|잔량|^기말$", r"수량|기초|전기"),
]


def _roles(cols):
    R = {}
    for k, rx, avoid in ROLE:
        last = k in ("amt", "done", "rem")  # 수량/금액이 짝인 표는 뒤쪽(금액) 열
        j = find_col(cols, rx, avoid, last=last)
        if j is not None and j not in R.values():
            R[k] = j
    if "own" in R and "cls" in R and R["own"] == R["cls"]:
        R.pop("cls")
    return R


def _group_from(ctx):
    for line in reversed(ctx[-3:]):
        line = re.sub(r"[\(\[]?\s*(기준일|단\s*위)\s*[:：].*$", "", line).strip()
        m = re.search(r"(?:\(\d+\)|\d+\)|[가-하]\)|<|\[)\s*([^()<>\[\]]{2,24}?)\s*(?:>|\])?\s*$", line)
        if m and re.search(r"관급|민간|해외|국내|도급|자체|토목|건축|주택|플랜트|인프라|공공|정비|단독|공동|부문", m.group(1)):
            return m.group(1).strip()
    return None


_CO = lambda s: re.sub(r"㈜|\(주\)|주식회사|\s", "", s or "")
ENT_RX = re.compile(r"^(?:\d+\)|\(\d+\)|[■●◎▶□○◆]|[가-하]\.)\s*(.{2,34})$")
COMPANYISH = re.compile(r"㈜|\(주\)|주식회사|엔지니어링|산업$|스틸|건설$|이앤씨|건축사|시티$|랜드|개발$|E&C|Co\.|Ltd|법인|종속")


def _entity(line, names):
    """'2) 현대엔지니어링'처럼 다른 회사 표가 시작되는 줄이면 True, 회사 자신이면 False, 아니면 None"""
    line = re.sub(r"[\(\[]?\s*(기준일|단\s*위)\s*[:：].*$", "", line).strip()
    if re.match(r"^(?:[가-하]\.|\d+\.|\(\d+\)|\d+\))\s*(?:주요\s*)?수주\s*(상황|현황|실적)", line):
        return False  # 새 '수주상황' 단락 → 다시 회사 자신
    m = re.search(r"(?:^|\s)(?:\d+\)|\(\d+\)|[■●◎▶□○◆]|[가-하]\.|\[)\s*([^\s\]][^\]]{1,33}?)\]?$", line)
    if not m:
        return None
    seg = m.group(1)
    if "지배회사" in seg:
        return False
    if "종속" in seg:
        return True
    nm, whole = _CO(re.sub(r"\s.*$", "", seg)), _CO(seg)
    if any(n and (n in whole or nm in n) for n in names):
        return False
    return True if COMPANYISH.search(seg) else None


def periodic_orders(rcept_no, nodes, names=(), log=print):
    cand = [n for n in nodes if ORD_NODE.search(n["text"]) and n["dcmNo"]]
    docs = []
    for n in cand:
        docs.append((n["text"], node_html(rcept_no, n)))
        time.sleep(0.25)
    return orders_from(docs, names)


def orders_from(docs, names=()):
    """docs: [(목차 이름, 원문 html)] → 수주 현장 목록. 상세표가 있으면 상세표만 쓴다"""
    is_detail = lambda t: "상세" in t or re.match(r"^\d+\.수주상황$", t)
    for part in ([d for d in docs if is_detail(d[0])], [d for d in docs if not is_detail(d[0])]):
        rows = []
        names = [_CO(n) for n in names if n]
        for title, html in part:
            ctx, in_order, other = [], bool(is_detail(title)), False
            for kind, x in blocks(html):
                if kind == "p":
                    ctx.append(x)
                    e = _entity(x, names)
                    if e is not None:
                        other = e
                    if re.search(r"수주\s*(상황|현황|실적|에\s*관한)|수주잔고|진행\s*중인\s*공사|계약잔액", x):
                        in_order = True
                    elif re.search(r"^[가-하]\.\s*(매출|판매)|^\(?\d\)?[\.\)]?\s*매출", x):
                        in_order = False
                    continue
                G = x
                if len(G) <= 2 and len(G[-1]) <= 2:  # 단위 · 기준일 · 안내문 상자
                    ctx.append(" ".join(dict.fromkeys(c for r in G for c in r)))
                    continue
                cols, data, u_in = split(G)
                if not cols or other:
                    continue
                R = _roles(cols)
                if "rem" not in R or not ({"nm", "own"} & R.keys()) or not ({"amt", "done"} & R.keys() or in_order):
                    continue
                if re.search(r"진행률", " ".join(cols)) and "amt" not in R:
                    continue
                u = u_in or unit_of(" ".join(ctx[-3:])) or unit_of(" ".join(cols)) or 1e2
                g0 = _group_from(ctx)
                for r in data:
                    r = r + [""] * (len(cols) - len(r))
                    get = lambda k: r[R[k]] if k in R else ""
                    nm, own, cls = get("nm"), get("own"), get("biz") or get("cls")
                    label = nm or own
                    rem, amt, done = num(get("rem")), num(get("amt")), num(get("done"))
                    if rem is None and amt is None:
                        continue
                    if TOTAL.search(_sp(label)) or (not label and TOTAL.search(_sp(" ".join(r[:2])))) or TOTAL.search(_sp(cls)):
                        continue
                    cd, ed = get("cd"), get("ed")
                    cd, ed = ("" if v in (nm, own) or not re.search(r"\d", v) else v for v in (cd, ed))
                    g = cls if cls and cls not in (nm, own) and not re.fullmatch(r"[\d\W]*", cls) else None
                    row = {"g": g or g0, "own": own if own != nm else "", "nm": nm or own, "cd": cd, "ed": ed,
                           "amt": EOK(amt, u), "done": EOK(done, u), "rem": EOK(rem, u)}
                    row["co"] = _sp(get("co"))
                    rows.append({k: v for k, v in row.items() if v not in (None, "", "-") or k == "nm"})
        # 종속회사 수주까지 함께 적은 표는 회사 자신(첫 회사) 것만
        cos = [x.get("co") for x in rows if x.get("co")]
        if cos and len(set(cos)) > 1:
            rows = [x for x in rows if x.get("co") in (None, cos[0])]
        for x in rows:
            x.pop("co", None)
        # 계열사별로 나눈 표(예: 두산에너빌리티 · 두산퓨얼셀 · 두산밥캣)는 회사 자신 행만
        own = [x for x in rows if x.get("g") and any(n and (n in _CO(x["g"]) or _CO(x["g"]) in n) for n in names)]
        if own and len(own) < len(rows) and all(x.get("g") for x in rows):
            rows = own
        # 여러 사업을 하는 회사(예: 건설부문 + 조선 · 중공업 · 무역)는 건설 쪽 수주만
        if any("건설" in (x.get("g") or "") for x in rows) and any(re.search(r"조선|중공업|제조|무역|상사|화약|방산|에너지|글로벌|모멘텀|비전|오션|에어로|기계", x.get("g") or "") for x in rows):
            rows = [x for x in rows if "건설" in (x.get("g") or "")]
        seen, uniq = set(), []  # 연결 · 별도 표가 겹치는 경우 등 같은 현장 중복 제거
        for x in rows:
            key = (_sp(x["nm"]), _sp(x.get("own", "")), x.get("amt"), x.get("rem"))
            if key not in seen:
                seen.add(key)
                uniq.append(x)
        if uniq:
            tot = {k: round(sum(x.get(k) or 0 for x in uniq), 1) for k in ("amt", "done", "rem")}
            return {"kind": "list", "rows": uniq, "sum": tot}
    return None


# ---------------------------------------------------------------- 감사보고서: 공사계약 잔액 변동
FLOW = [  # (키, 열 이름, 피할 이름, 뒤쪽 열 우선)
    ("beg", r"기초|전기말계약잔액|전기말잔액", r"^전기$", False),
    ("end", r"기말|당기말|당기공사계약잔액|미인식|잔액|잔고", r"기초|전기", True),
    ("rev", r"수익|매출|기성|감소|인식|계상|수입", r"기초|기말|미인식|누적|^기수익|전기말", True),
    ("amt", r"^(총)?(도급|계약)(금액|액)$|^총계약금액$|^도급금액$", None, False),
]
FLOW_NEW = r"신규|증감|변경|추가|증가|수주"


def _section(blocks_, start_rx, stop_rx=r"^\d{1,2}\s*[\.\)]\s*[가-힣]"):
    """주석 하나(번호 머리글부터 다음 번호 머리글 전까지)의 블록"""
    on, out = False, []
    for kind, x in blocks_:
        if kind == "p" and re.search(start_rx, _sp(x)) and re.match(r"^\s*\d{1,2}\s*[\.\)]", x):
            on = True
            out.append((kind, x))
            continue
        if on and kind == "p" and re.match(stop_rx, x.strip()) and not re.search(start_rx, _sp(x)):
            m = re.match(r"^\s*(\d{1,2})", x)
            if m:
                break
        if on:
            out.append((kind, x))
    return out


def audit_orders(html):
    B = list(blocks(html))
    ctx, best = [], None
    for kind, x in B:
        if kind == "p":
            ctx.append(x)
            continue
        G = x
        if len(G) == 1:
            ctx.append(" ".join(G[0]))
            continue
        c = " ".join(ctx[-3:])
        if not re.search(r"계약\s*잔액|계약금액의\s*변동|계약액|공사계약|건설계약\s*잔액|수주\s*잔", _sp(c) + " " + _sp(" ".join(G[0]))):
            continue
        if re.search(r"분양", " ".join(ctx[-1:])) and not re.search(r"공사", " ".join(ctx[-1:])):
            continue
        cols, data, u_in = split(G)
        u = u_in or unit_of(" ".join(ctx[-4:])) or unit_of(" ".join(cols)) or 1e5
        txtall = " ".join(cols)
        rows = []
        # 가로형(열: 기초 · 신규 · 수익 · 기말)
        J = {}
        for k, rx, avoid, last in FLOW:
            j = find_col(cols, rx, avoid=avoid, last=last)
            if j is not None and j not in J.values() and j > 0:
                J[k] = j
        JN = [j for j, c in enumerate(cols) if j > 0 and j not in J.values() and re.search(FLOW_NEW, c) and not re.search(r"기초|기말|잔액", c)]
        if "end" in J and ({"beg", "amt"} & J.keys()) and (JN or "rev" in J):
            for r in data:
                r = r + [""] * (len(cols) - len(r))
                lab = " ".join(dict.fromkeys(v for v in r[:min([*J.values(), *JN])] if v and not re.fullmatch(r"당\s*기|전\s*기|당기말|전기말|\d{4}년?", _sp(v))))
                vals = {k: num(r[j]) for k, j in J.items()}
                if vals.get("end") is None or TOTAL.search(_sp(lab)):
                    continue
                if JN:
                    nv = [num(r[j]) for j in JN]
                    vals["new"] = sum(v for v in nv if v is not None) if any(v is not None for v in nv) else None
                if vals.get("rev") is not None:
                    vals["rev"] = abs(vals["rev"])
                rows.append({"nm": lab or "공사", **{k: EOK(v, u) for k, v in vals.items() if v is not None}})
        else:  # 세로형(행: 기초 · 신규 · 수익 · 기말, 열: 당기 · 전기)
            vals = {}
            for r in data:
                lab = _sp(r[0])
                v = next((num(c) for c in r[1:] if num(c) is not None), None)
                for k, rx, avoid, _ in [*FLOW, ("new", FLOW_NEW, r"기초|기말", False)]:
                    if k not in vals and re.search(rx, lab) and not (avoid and re.search(avoid, lab)) and v is not None:
                        vals[k] = v
                        break
            if {"beg", "end"} <= vals.keys():
                rows.append({"nm": "공사 전체", **{k: EOK(abs(v) if k == "rev" else v, u) for k, v in vals.items() if k != "amt"}})
        if not rows:
            continue
        near = " ".join(ctx[-2:])[-40:] + " " + " ".join(G[0][:1])
        cur = re.search(r"전\s*기", near) and not re.search(r"당\s*기", " ".join(G[0][:1]) + " ".join(ctx[-1:])[-20:])
        cand = {"rows": rows, "cur": not cur}
        # 당기 표 중에서는 현장별로 가장 자세한(행이 많은) 표
        if best is None or (cand["cur"], len(rows)) > (best["cur"], len(best["rows"])):
            best = cand
    if not best:
        return None
    rows = best["rows"]
    s = {k: round(sum(x.get(k) or 0 for x in rows), 1) for k in ("amt", "beg", "new", "rev", "end") if any(k in x for x in rows)}
    return {"kind": "flow", "rows": rows, "sum": s}


# ---------------------------------------------------------------- 보증 · 약정
PF_ROLE = [
    ("region", r"사업지역|^지역", None),
    ("use", r"사업장구분|사업장$|사업구분|사업장종류", r"^PF종류|^PF구분"),
    ("pf", r"PF종류|PF구분|PF유형", None),
    ("kind", r"신용보강", None),
    ("lim", r"보증한도.*전체|보증한도$|보증한도/", r"당사|연결|전기"),
    ("share", r"부담률|부담율", None),
    ("amt", r"보증금액", r"전기"),
    ("debtor", r"채무자|차주|발행회사", r"특수|관계|여부"),
    ("lender", r"채권기관|대주", None),
    ("loan", r"대출잔액|미상환잔액", r"전기"),
    ("mat", r"만기일|만기|대출기간/종료|종료일", r"조기"),
    ("period", r"대출기간", None),
    ("cmpl", r"책임준공", None),
]
NOT_CUR = re.compile(r"전\s*기\s*말?\s*[>\)]?\s*$|<\s*전\s*기")


def _pf_roles(cols):
    R = {}
    for k, rx, avoid in PF_ROLE:
        j = find_col(cols, rx, avoid)
        if j is not None and j not in R.values():
            R[k] = j
    if "amt" not in R:  # '보증한도(전체) · 부담률 · 보증한도(당사분)' 처럼 보증금액 열이 없는 표
        hs = [j for j, c in enumerate(cols) if "보증한도" in c and "전기" not in c]
        if len(hs) >= 2:
            R["lim"], R["amt"] = hs[0], hs[-1]
    return R


def _rowtxt(r, j):
    return r[j] if j is not None and j < len(r) else ""


def guarantees_from(htmls, std=True):
    """원문(정기보고서 '우발부채 등' 단락 또는 감사보고서 주석) → 보증 · 약정 요약"""
    sites, spc, summ, raw = [], [], {}, []
    seen = set()
    for html in htmls:
        ctx, grp = [], None
        for kind, x in blocks(html):
            if kind == "p":
                ctx.append(x)
                m = re.search(r"(정비사업|기타사업|도시정비|재건축|재개발)[^.]{0,20}(관련|PF)", x)
                if m:
                    grp = "정비사업" if re.search(r"정비|재건축|재개발", m.group(1)) else "기타사업"
                continue
            G = x
            if len(G) <= 2 and len(G[-1]) <= 2:
                ctx.append(" ".join(dict.fromkeys(c for r in G for c in r)))
                continue
            near = " ".join(ctx[-2:])
            cols, data, u_in = split(G)
            if not cols:
                continue
            u = u_in or unit_of(" ".join(ctx[-3:])) or unit_of(" ".join(cols)) or 1e5
            cj = " ".join(cols)
            if NOT_CUR.search(near[-30:]) or re.match(r"^전\s*기", G[0][0] if G[0] else ""):
                continue
            if "신용보강" in cj and re.search(r"증권종류|발행금액", cj):  # PF 유동화증권 신용보강(SPC 발행분)
                J = {k: find_col(cols, rx) for k, rx in (("spc", r"발행회사|SPC"), ("sec", r"증권종류"), ("use", r"사업장"),
                                                       ("issued", r"발행일"), ("iss", r"발행금액"), ("bal", r"미상환|잔액"), ("kind", r"신용보강"))}
                for r in data:
                    if TOTAL.search(_sp(" ".join(r[:2]))) or not any(is_num(c) for c in r):
                        continue
                    row = {k: (EOK(num(_rowtxt(r, j)), u) if k in ("iss", "bal") else _rowtxt(r, j)) for k, j in J.items() if j is not None}
                    row = {k: v for k, v in row.items() if v not in (None, "", "-")}
                    if row.get("bal") and row["bal"] > 50000:
                        continue
                    key = tuple(sorted((k, str(v)) for k, v in row.items()))
                    if row.get("bal") is not None and key not in seen:
                        seen.add(key)
                        spc.append(row)
                continue
            if "신용보강" in cj and re.search(r"보증|대출|잔액", cj):
                R = _pf_roles(cols)
                for r in data:
                    lab = _sp(" ".join(r[:2]))
                    if TOTAL.search(lab) or not any(is_num(c) for c in r):
                        continue
                    g = lambda k: _rowtxt(r, R.get(k))
                    row = {"grp": grp, "region": g("region"), "use": g("use"), "pf": g("pf"), "kind": g("kind"),
                           "lim": EOK(num(g("lim")), u), "share": g("share"), "amt": EOK(num(g("amt")), u), "debtor": g("debtor"),
                           "lender": g("lender"), "loan": EOK(num(g("loan")), u), "mat": g("mat") or g("period"),
                           "cmpl": EOK(num(g("cmpl")), u)}
                    row = {k: v for k, v in row.items() if v not in (None, "", "-")}
                    if not (row.get("amt") or row.get("loan") or row.get("lim")) or max(row.get(k) or 0 for k in ("amt", "loan", "lim")) > 50000:
                        continue
                    key = tuple(sorted((k, str(v)) for k, v in row.items() if k != "grp"))
                    if key in seen:
                        continue
                    seen.add(key)
                    sites.append(row)
                continue
            # 요약 표: 책임준공 · 중도금대출 · SOC
            kind_ = "cmpl" if re.search(r"책임\s*준공", near[-200:]) and re.search(r"도급금액|약정금액|공사건수", cj) else \
                    "mid" if re.search(r"중도금", near[-200:]) and re.search(r"보증|대출", cj) else \
                    "soc" if re.search(r"SOC", near[-200:]) and re.search(r"보증", cj) else None
            if not std:  # 감사보고서 주석은 서식이 제각각이라 요약하지 않고 원문 표 그대로
                kind_ = None
            if kind_ and kind_ not in summ:
                want = {"n": r"건수", "contract": r"도급금액", "cmplamt": r"약정금액", "lim": r"보증한도", "loan": r"대출잔액", "amt": r"보증금액"}
                J = {}
                for k, rx in want.items():
                    j = find_col(cols, rx, avoid=r"전기|전체\)?\s*$" if k != "lim" else r"전기")
                    if j is not None:
                        J[k] = j
                body = [r for r in data if any(is_num(c) for c in r)]
                mine = [r for r in body if re.search(r"당사분|당사|연결실체", " ".join(r[:3]))]
                tot = [r for r in body if TOTAL.search(_sp(" ".join(r[:2])))]
                use = mine or tot or [r for r in body if not re.search(r"전체", " ".join(r[:3]))] or body
                if len(use) > 1 and tot and not mine:
                    use = tot[-1:]
                v = {}
                for k, j in J.items():
                    vals = [num(_rowtxt(r, j)) for r in use]
                    vals = [x for x in vals if x is not None]
                    if vals:
                        v[k] = sum(vals) if k == "n" else EOK(sum(vals), u)
                if v and all(x >= 0 for x in v.values()):
                    summ[kind_] = v
                continue
            # 그 밖에 남에게 제공한 보증 · 약정(원문 표 그대로)
            if re.search(r"제공한|제공하고|지급보증|연대보증|자금보충|채무인수|책임준공|보증\s*내역", near[-220:]) and \
               not re.search(r"제공받|제공\s*받|소송|피고|원고|담보|견질|어음|수표|계약이행|하자|분양보증|공제조합으로부터", near[-220:]) and \
               re.search(r"보증|약정|한도|금액", cj) and len(data) >= 1 and len(raw) < 12:
                rows = [r for r in data if any(is_num(c) for c in r) and not re.match(r"^\(?주\d|^\(\*", r[0] or "")]
                if rows:
                    lines = [c for c in ctx[-4:] if not re.match(r"^\s*[\(<\[]?\s*(단\s*위|기준일|당\s*기|전\s*기)", c)]
                    cap = re.sub(r"\s*\(?단\s*위.*$", "", re.split(r"(?<=다\.)\s+(?=\S)", lines[-1] if lines else "")[-1])[-120:]
                    unit_txt = next((k for k, v in UNITS.items() if v == u), "")
                    suf = {c.split("/")[-1] for c in cols if "/" in c}
                    if len(suf) == 1 and all("/" in c for c in cols):  # '제공받은자/종속기업'처럼 구역 이름이 머리글에 붙은 경우
                        cols = [c.rsplit("/", 1)[0] for c in cols]
                    raw.append({"cap": cap, "unit": unit_txt, "cols": cols, "rows": rows[:500]})
    if not (sites or spc or summ or raw):
        return None
    out = {}
    if spc:
        out["spc"] = spc
        out["spc_sum"] = round(sum(x.get("bal") or 0 for x in spc), 1)
    if sites:
        out["pf"] = sites
        out["pf_sum"] = {k: round(sum(x.get(k) or 0 for x in sites), 1) for k in ("amt", "loan")}
    if summ:
        out["sum"] = summ
    if raw and (not std or not (sites or summ or spc)):  # 표준 서식(PF 보증 표)이 없는 회사만 원문 표를 그대로
        out["raw"] = raw
    return out


# ---------------------------------------------------------------- 회사별 수집
PERIODIC = re.compile(r"사업보고서|반기보고서|분기보고서")
NOTE_NODE = re.compile(r"우발|약정|지급보증|보증")


def _asof(nm):
    m = re.search(r"\((\d{4})\.(\d{2})\)", nm or "")
    return f"{m.group(1)}.{m.group(2)}" if m else ""


def _latest_periodic(D, corp_code, today):
    import datetime as dt
    d = D.json("list.json", corp_code=corp_code, bgn_de=(today - dt.timedelta(days=400)).strftime("%Y%m%d"),
               pblntf_ty="A", page_count=100)
    xs = [x for x in d.get("list", []) if PERIODIC.search(x.get("report_nm", ""))] if d.get("status") == "000" else []
    if not xs:
        return None
    x = max(xs, key=lambda x: x["rcept_no"])
    rd = x["rcept_dt"]
    return {"no": x["rcept_no"], "nm": re.sub(r"\s+", " ", x["report_nm"]).strip(), "d": f"{rd[:4]}-{rd[4:6]}-{rd[6:8]}"}


def parse_report(src, names, log=print):
    nodes = audit_fs._nodes(src["no"])
    if src["kind"] == "per":
        ords = periodic_orders(src["no"], nodes, names, log)
        main = [n for n in nodes if re.match(r"^\d+\.우발부채등에관한사항$", n["text"]) and n["dcmNo"]]
        htmls = [node_html(src["no"], n) for n in main[:1]]
        cg = guarantees_from(htmls) if htmls else None
        if not cg or not (cg.get("pf") or cg.get("sum")):  # '연결재무제표 주석 참고'로 넘긴 회사는 별도 주석의 우발부채 단락
            notes = [n for n in nodes if NOTE_NODE.search(n["text"]) and "연결" not in n["text"] and n["dcmNo"] and n not in main]
            extra = [node_html(src["no"], n) for n in notes[:3]]
            cg2 = guarantees_from(htmls + extra) if extra else None
            if cg2 and (cg2.get("pf") or cg2.get("sum") or not cg):
                cg = cg2
    else:
        pick = [n for n in nodes if n["text"] == "주석" and n["dcmNo"]] or [n for n in nodes if n["text"].startswith("(첨부)") and n["dcmNo"]]
        html = node_html(src["no"], pick[0]) if pick else b""
        ords = audit_orders(html) if html else None
        cg = guarantees_from([html], std=False) if html else None
    return ords, cg


AUD_SEP = re.compile(r"^감사보고서\s*\((\d{4})\.(\d{2})\)")


def _separate_audit(D, corp_code, src):
    """연결감사보고서를 낸 회사는 같은 사업연도의 (별도)감사보고서 — 상장 자회사 등의 수주 · 보증이 섞이지 않게"""
    m = re.search(r"\((\d{4})\.(\d{2})\)", src.get("nm", ""))
    if not m:
        return None
    d = D.json("list.json", corp_code=corp_code, bgn_de=f"{int(m.group(1)) + 1}0101", pblntf_ty="F", page_count=100)
    xs = [x for x in d.get("list", []) if (mm := AUD_SEP.match(re.sub(r"\s+", " ", x.get("report_nm", "")).strip()))
          and mm.groups() == m.groups()] if d.get("status") == "000" else []
    if not xs:
        return None
    x = max(xs, key=lambda x: x["rcept_no"])
    rd = x["rcept_dt"]
    return {"no": x["rcept_no"], "nm": re.sub(r"\s+", " ", x["report_nm"]).strip(), "d": f"{rd[:4]}-{rd[4:6]}-{rd[6:8]}", "kind": "aud"}


def collect(D, co, names_of, prev, today, log=print):
    """co: dart.json 의 회사별 데이터. 정기보고서(없으면 감사보고서)가 바뀐 회사만 원문을 다시 읽는다."""
    out, errors = {}, []
    for k, v in co.items():
        p = (prev or {}).get(k) or {}
        f = v.get("fin") or {}
        src = None
        per = [x for x in v.get("disc", []) if PERIODIC.search(x["nm"])]
        if per:
            x = max(per, key=lambda x: x["no"])
            src = {"no": x["no"], "nm": x["nm"], "d": x["d"], "kind": "per"}
        elif f and f.get("src") != "감사보고서" and D is not None:
            try:
                x = _latest_periodic(D, v["corp_code"], today)
                if x:
                    src = {**x, "kind": "per"}
            except Exception as e:
                errors.append(f"정기보고서 목록 {k}: {str(e)[:40]}")
        if not src and f.get("fy_rcept") and f.get("src") != "감사보고서":  # 최근 400일에 정기보고서가 없는 회사(합병 등)는 재무 요약에 쓴 사업보고서
            y = max((x["p"] for x in f.get("periods", []) if x.get("t") == "FY"), default="")
            src = {"no": f["fy_rcept"], "nm": f"사업보고서 ({y}.12)" if y else "사업보고서", "d": "", "kind": "per"}
        if not src:
            a = v.get("audit") or ({"no": f["audit_no"], "nm": f.get("audit_nm", ""), "d": ""} if f.get("audit_no") else None)
            if a:
                src = {"no": a["no"], "nm": a.get("nm", ""), "d": a.get("d", ""), "kind": "aud"}
                if src["nm"].startswith("연결") and D is not None:
                    try:
                        src = _separate_audit(D, v["corp_code"], src) or src
                    except Exception as e:
                        errors.append(f"감사보고서 목록 {k}: {str(e)[:40]}")
        if not src:
            if p:
                out[k] = p
            continue
        if p.get("no") == src["no"] and p.get("v") == VERSION:
            out[k] = p
            continue
        try:
            ords, cg = parse_report(src, names_of(k), log)
            out[k] = {"v": VERSION, **src, "asof": _asof(src["nm"]), **({"ord": ords} if ords else {}), **({"cg": cg} if cg else {})}
        except Exception as e:
            errors.append(f"수주·보증 {k}: {str(e)[:60]}")
            if p:
                out[k] = p
        time.sleep(0.3)
    return out, errors
