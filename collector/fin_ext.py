"""주요 재무지표용 추가 항목 — DART 공시뷰어 원문(키 불필요)에서 읽는다.

재무 API(주요계정)에는 차입금 · 현금성자산 · 감가상각비 · 이자비용이 없어서, 사업 · 반기 · 분기보고서나
감사보고서 원문의 재무상태표 · 현금흐름표와 주석에서 같은 기준(연결 또는 별도)의 숫자를 읽는다.

  debt  총차입금 = 단기차입금 + 유동성장기차입금 · 유동성사채 + 사채 + 장기차입금 + 리스부채(유동 · 비유동) 등
        — 재무상태표에 리스부채 행이 없으면(기타금융부채에 포함) 주석의 리스부채 합계를 더한다
  cash  현금성자산 = 현금및현금성자산 + 단기금융상품 · 단기금융자산 · 단기투자자산 · 단기예금 + 유동 당기손익-공정가치 금융자산
        — 단기금융상품이 기타금융자산에 묶여 있으면 그 구성 주석에서 읽는다
  da    감가상각비 + 무형자산상각비(사용권자산 · 투자부동산 상각 포함) — 현금흐름표 조정 항목, 없으면 '영업에서 창출된 현금' 주석
  int   이자비용 — 같은 표, 없으면 금융원가(금융비용) 주석
  arl   매출채권처분손실, cap 자본화된 차입원가 — 주석(금융비용 = 이자비용 + 매출채권처분손실 + 자본화된 차입원가)
금액은 억원. 찾지 못한 항목은 넣지 않는다(화면에는 '–').
"""
import re, time

from bs4 import BeautifulSoup

import audit_fs

VERSION = 13
_sp = audit_fs._sp

DEBT = re.compile(r"차입금|차입부채|사채|리스부채|유동화채무|유동성장기(?:부채|채무)|^장기부채$")  # '유동차입금(사채포함)', '단기차입금및유동성차입금' 같은 이름도
# 주석 표에서는 이름이 정확히 맞는 항목만(합계 · 소계 행과 겹치지 않게)
DEBT_STRICT = re.compile(r"^(?:유동성|비유동성?|유동|단기|장기)?(?:장기)?(?:차입금|사채|전환사채|교환사채|신주인수권부사채|리스부채|유동화채무|장기부채)$|^유동성장기(?:부채|채무)$")
FIN_LIAB = re.compile(r"^(?:단기|장기|유동|비유동)금융(?:상품)?부채$")  # 차입금을 금융부채로 묶어 표시한 재무상태표 → 주석에서 차입금 구성을 읽는다
DEBT_NOT = re.compile(r"할인|할증|조정|상환권|전환권|미지급|이자|보증|충당|대여|채권|자산|미수|선급|발행비|파생|합계|총계|소계|공사채무|청구|선수|예수|임대")
CASH = re.compile(r"^현금(?:및|과)현금(?:성자산|등가물)$|^현금및예치금$")
STFI = re.compile(r"^(?:단기금융상품(?:자산)?|단기금융자산|단기투자자산|단기예금|단기예치금|단기투자증권)$")
# 유동자산의 당기손익-공정가치 금융자산(단기 운용) — 재무상태표 유동 구간에서만
FVPL = re.compile(r"^(?:유동|단기)?(?:당기손익(?:-?공정가치)?(?:측정|인식)?(?:지정)?(?:금융자산|금융상품)|단기매매(?:금융)?(?:자산|증권)|상각후원가(?:로)?측정(?:하는)?(?:유동)?금융자산)$")
OTH_FA = re.compile(r"^(?:유동|단기)?기타(?:유동)?금융자산$")  # 단기금융상품이 묶일 수 있는 행
LEASE_TOT = re.compile(r"^(?:총리스부채|리스부채합계|리스부채총계|리스부채계|리스부채의합계)$")
LEASE_C = re.compile(r"^(?:유동|단기|유동성)리스부채$")
LEASE_N = re.compile(r"^(?:비유동|장기)리스부채$")
LEASE_ANY = re.compile(r"^(?:유동|비유동|단기|장기|유동성)?리스부채$")
OTH_FL = re.compile(r"^(?:유동|단기|비유동|장기)?기타(?:유동|비유동)?금융부채$")  # 리스부채가 묶일 수 있는 행
ARL = re.compile(r"^매출채권(?:의)?(?:처분|매각)손실$")
CAPI = re.compile(r"^(?:당기)?(?:유형자산|재고자산|투자부동산|무형자산|적격자산)?(?:의)?자본화(?:된|한)(?:차입원가|금융원가|금융비용|이자비용|이자)(?:금액)?(?:합계)?$")
_DA_P = r"(?:유형자산|무형자산|투자부동산|사용권자산|리스사용권자산|리스자산|운휴자산|리스)"
DA = re.compile(rf"^{_DA_P}?감가상각비$|^{_DA_P}?상각비$|^감가상각비,?{_DA_P}$|^감가상각비및(?:무형자산)?상각비$|^감가상각비와(?:무형자산)?상각비$")
INT = re.compile(r"^이자비용$")
PERIOD_RX = audit_fs.PERIOD_RX


def _label(s):
    l = audit_fs._label(re.sub(r"^[\s\-–—―‐－·ㆍ•∙※▶▷►○●□■◦]+", "", s or ""))
    l = re.sub(r"\(주석?[\d,\.\s]*\)?|\(\*\d*\)|\*\d*$|<[^>]*>", "", l)
    l = re.sub(r"[\(\)（）]", "", l)
    # 번호 붙은 항목 '1. 현금및현금성자산' · 'Ⅰ. 유동자산' · '(1) …' → 번호를 뗀다('1년이내' 같은 기간은 그대로)
    l = re.sub(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹ]+|\d{1,2})\.?(?=[가-힣])(?!년|개월|분기|월|일|차|회|기)", "", l)
    return re.sub(r"(?:에대한)?조정(?:액)?$|가산$", "", l)  # 현금흐름 조정 표의 '감가상각비에 대한 조정' → 감가상각비


UNIT_ROW = re.compile(r"^\(?\s*단\s*위\s*[:：]?[^)]*\)?$")


def _trs(t):
    """표의 행 — 맨 앞의 '(단위: 천원)'만 있는 행은 뺀다(그다음 행이 머리글)"""
    trs = t.find_all("tr")
    while trs and UNIT_ROW.match(_sp(trs[0].get_text(" ", strip=True)) or "x"):
        trs = trs[1:]
    return trs


def _rows(t):
    """표 하나 → [(항목, [기간별 값])] — 머리글의 기간 수로 열을 나누고, 기간마다 첫 숫자 칸을 쓴다.
    분기 · 반기 표처럼 '3개월 · 누적' 칸이 있으면 누적 칸을 쓴다."""
    trs = _trs(t)
    if len(trs) < 2:
        return [], 0
    head = [c.get_text(" ", strip=True) for c in trs[0].find_all(["th", "td"])]
    hs = [_sp(h) for h in head]
    has_note = any("주석" in h for h in hs)
    np_ = sum(1 for h in hs[1:] if PERIOD_RX.search(h) and "주석" not in h) or (1 if any("공시금액" in h for h in hs) else 2)
    cum = None
    if len(trs) > 1 and any("누적" in _sp(c.get_text()) for c in trs[1].find_all(["th", "td"])):
        sub = [_sp(c.get_text()) for c in trs[1].find_all(["th", "td"])]
        cum = [i for i, h in enumerate(sub) if "누적" in h]
    out = []
    for tr in trs[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        lab = _label(cells[0])
        vals = cells[2:] if has_note and len(cells) >= np_ + 2 else cells[1:]
        nums = [audit_fs._num(v) for v in vals]
        if not any(n is not None for n in nums):
            continue
        if cum and len(nums) >= 2 * len(cum):  # [3개월, 누적, 3개월, 누적] → 누적만
            per = [nums[j] for j in range(len(nums)) if j % 2 == 1][:len(cum)]
        else:
            size = max(1, len(nums) // np_)
            per = []
            for i in range(np_):
                g = [n for n in nums[i * size:(i + 1) * size if i < np_ - 1 else None] if n is not None]
                per.append(g[0] if g else None)  # 감사보고서처럼 항목(안쪽 칸) · 소계(바깥 칸)로 나뉜 표는 항목 칸 값
        out.append((lab, per))
    return out, np_


def _unit_of(t):
    tr = t.find("tr")
    if tr is not None and UNIT_ROW.match(_sp(tr.get_text(" ", strip=True)) or "x"):  # 표 안 첫 행의 단위
        return audit_fs._unit(tr.get_text(" ", strip=True))
    s = t.find_previous(string=re.compile(r"단\s*위"))
    return audit_fs._unit(s) if s else 1e8


def _html(rcept_no, node):
    r = audit_fs._get(f"{audit_fs.VIEW}/report/viewer.do",
                      params={"rcpNo": rcept_no, "dcmNo": node["dcmNo"], "eleId": node["eleId"],
                              "offset": node["offset"], "length": node["length"], "dtd": node["dtd"]})
    return BeautifulSoup(r.content, "html.parser")


def _pick_nodes(nodes, cons):
    """→ (재무상태표 · 현금흐름표가 든 노드들, 주석 노드들)"""
    st, notes = [], []
    tx = lambda n: _sp(n["text"])
    has_c = any("연결" in tx(n) and re.search(r"재무상태표|재무제표$", tx(n)) for n in nodes)
    for n in nodes:
        t = tx(n)
        if not n.get("dcmNo"):
            continue
        is_c = "연결" in t
        if cons != is_c and has_c:
            # 사업보고서 '주석' 하위 노드: 연결은 '(연결)'이 붙고 별도는 안 붙는다
            continue
        if re.search(r"재무상태표$|대차대조표$|현금흐름표$", t):
            st.append(n)
        elif re.search(r"^\(첨부\)(연결)?재무제표$", t):
            st.append(n)
        elif re.search(r"주석$|^\d+(-\d+)?\.", t) and not re.search(r"요약재무|재무에관한사항|회사의개요|사업의내용|이사회|주주|임원|계열회사|감사인|그밖에|상세표|배당|자금조달|기타재무", t):
            notes.append(n)
    if not st:  # 예전 사업보고서: '2.연결재무제표' · '4.재무제표' 한 노드에 재무제표가 모두 들어 있다
        st = [n for n in nodes if n.get("dcmNo") and re.match(r"^\d+\.(연결)?재무제표$", tx(n)) and (("연결" in tx(n)) == cons or not has_c)]
        notes = [n for n in notes if n not in st]
    if not st:  # 감사보고서에서 연결 · 별도 노드 이름이 따로 없는 경우
        st = [n for n in nodes if n.get("dcmNo") and re.search(r"^\(첨부\)", tx(n))]
    if not notes:
        notes = [n for n in nodes if n.get("dcmNo") and tx(n) == "주석"]
    return st, notes


def _scan_tables(soup, want_bs=True):
    """→ {"bs": [(rows, unit)], "flows": [(rows, unit, ctx)]}"""
    bs, flows = [], []
    for t in soup.find_all("table"):
        if t.find_parent("table") is not None:
            continue
        rows, np_ = _rows(t)
        if not rows:
            continue
        labs = [l for l, _ in rows]
        u = _unit_of(t)
        prev = t.find_previous(string=re.compile(r"\S"))
        ctx = ""
        p = t
        for _ in range(4):
            p = p.find_previous(["p", "td", "span"]) if p else None
            if p is not None and p.find_parent("table") is None:
                ctx = p.get_text(" ", strip=True)[:200] + " " + ctx
        if want_bs and any(CASH.match(l) for l in labs) and any(re.search(r"자산총계|부채총계|유동부채", l) for l in labs):
            bs.append((rows, u))
        if any(is_da(l) or INT.match(l) for l in labs):
            # 손익계산서의 감가상각비는 판관비 몫뿐이라 감가상각비는 현금흐름표(조정 항목) · 그 주석에서만 읽는다
            is_cf = any(re.search(r"영업활동|현금흐름|창출된현금|현금유출|현금유입|조정", l) for l in labs) or bool(CASH_NOTE.search(ctx))
            hs = [_sp(c.get_text()) for c in t.find_all("tr")[0].find_all(["th", "td"])]
            flows.append((rows, u, ctx, is_cf, hs))
    return bs, flows


def _bs_vals(rows, u):
    """재무상태표 → 기간별 {debt, cash, lease_row, ofa} — lease_row: 리스부채 행이 따로 있음, ofa: 유동 기타금융자산(억원)"""
    np_ = max(len(p) for _, p in rows)
    D = [0.0] * np_; C = [None] * np_; S = [0.0] * np_; O = [0.0] * np_; L = [0.0] * np_
    OFL = [[] for _ in range(np_)]
    seen_debt = seen_stfi = False
    lease_row = any("리스" in l for l, _ in rows if not re.search(r"자산", l))
    cur = True  # 유동자산 구간
    for lab, per in rows:
        if re.match(r"^비유동자산|^부채", lab) or lab.startswith("유동부채"):
            cur = False
        if DEBT.search(lab) and not DEBT_NOT.search(lab):
            seen_debt = True
            for i, v in enumerate(per):
                if v is not None:
                    D[i] += v
                    if "리스" in lab:
                        L[i] += v
        elif CASH.match(lab) and C[0] is None:
            C = [v for v in per] + [None] * (np_ - len(per))
        elif STFI.match(lab) or (cur and FVPL.match(lab)):
            seen_stfi = seen_stfi or bool(STFI.match(lab))
            for i, v in enumerate(per):
                if v is not None:
                    S[i] += v
        elif OTH_FL.match(lab):
            for i, v in enumerate(per):
                if v:
                    OFL[i].append(v / u)
        elif cur and OTH_FA.match(lab):
            for i, v in enumerate(per):
                if v:
                    O[i] += v
    r = lambda v: None if v is None else round(v / u, 1)
    out = []
    for i in range(np_):
        e = {}
        if seen_debt:
            e["debt"] = r(D[i])
        if C[i] is not None:
            e["cash"] = r(C[i] + S[i])
        e["_lease_row"] = lease_row
        e["_bsl"] = L[i] / u
        e["_ofl"] = OFL[i]
        e["_liab"] = next((pv[i] / u for l, pv in rows if l == "부채총계" and len(pv) > i and pv[i]), None)
        if O[i] and not seen_stfi:
            e["_ofa"] = O[i] / u
        out.append(e)
    return out


def _lease_note(soup):
    """주석의 리스부채 합계 → [당기, 전기](억원, 못 찾으면 None)"""
    found = []
    for t in soup.find_all("table"):
        if t.find_parent("table") is not None:
            continue
        trs = _trs(t)
        if not trs or len(trs) > 25:
            continue
        hs = [_sp(c.get_text()) for c in trs[0].find_all(["th", "td"])]
        if not _cur_head(hs):
            continue
        rows, np_ = _rows(t)
        if np_ > 3 or not rows:  # 당기말 · 전기말 · 전기초 표도 앞 두 칸
            continue
        u = _unit_of(t)
        vals = [None, None]
        # '리스부채' 제목 행 아래 '유동 · 비유동 · 합계'로 나눈 표
        heads = [_label(tr.find(["td", "th"]).get_text(" ", strip=True)) for tr in trs[1:] if tr.find(["td", "th"])]
        sub = "리스부채" in heads and "리스부채" not in [l for l, _ in rows]
        for j in range(min(np_, 2)):
            g = lambda rx: [pv[j] for l, pv in rows if (rx.match(l) or (sub and rx.match(l + "리스부채")))
                            and len(pv) > j and pv[j] is not None]
            tot, c, n = g(LEASE_TOT), g(LEASE_C), g(LEASE_N)
            plain = [pv[j] for l, pv in rows if l == "리스부채" and len(pv) > j and pv[j] is not None]
            v = None
            if c and n:
                v = c[0] + n[0]
            elif n and plain:
                # '리스부채(합계) · 리스부채(유동) · 비유동리스부채' 처럼 나열된 표: 합계 = 유동 + 비유동 인 값
                for a in plain:
                    if any(abs(a - b - n[0]) <= max(1, abs(a) * 1e-4) for b in plain if b is not a):
                        v = a
                        break
            elif tot and not any(re.search(r"할인되지않은|\d년(?:미만|이내|초과|이하|이상)|개월", l) for l, _ in rows):
                v = tot[0]
            if v is not None and v > 0:
                vals[j] = v / u
        if vals[0] is not None:
            found.append((vals, np_, "공시금액" in "".join(hs)))
    if not found:
        return [None, None]
    v0, np_, xb = found[0]
    v1 = v0[1] if np_ >= 2 else (found[1][0][0] if xb and len(found) > 1 and found[1][2] else None)
    return [v0[0], v1]


STFI_N = re.compile(r"^(?:장단기|단기)금융상품$|^단기예금$|^정기예금$")
TOT_N = re.compile(r"합계|소계|^계$")


def _comp(soup, target, item_rx):
    """구성 주석 표에서 합계(소계)가 재무상태표 행 값(target, 억원)과 같은 칸 → 그 합계 위(앞 합계 다음부터)
    item_rx 행의 합(억원). 맞는 구성 표에 해당 행이 없으면 0, 맞는 표가 없으면 None"""
    if target is None:
        return None
    zero = False
    for t in soup.find_all("table"):
        if t.find_parent("table") is not None:
            continue
        u = _unit_of(t)
        seg = []
        for tr in t.find_all("tr")[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            k0 = next((i for i, c in enumerate(cells) if audit_fs._num(c) is not None), len(cells))
            labs = [c for c in cells[:k0] if _sp(c)]
            if not labs or k0 >= len(cells):
                continue
            l = _label(labs[-1])  # 'XBRL 표: 유동 | 장단기금융상품 | 숫자…' 처럼 이름 칸이 둘인 경우 안쪽 이름
            nums = [audit_fs._num(v) for v in cells[k0:]]
            if TOT_N.search(l):
                for k, v in enumerate(nums):
                    if v is not None and abs(v / u - target) <= max(0.05, abs(target) * 1e-4):
                        hit = [n[k] or 0 for ll, n in seg if len(n) > k and item_rx.search(ll)]
                        if hit:
                            return sum(hit) / u
                        zero = zero or len(seg) >= 2
                        break
                seg = []
            else:
                seg.append((l, nums))
    v = _comp_t(soup, target, item_rx)  # 항목이 열로 놓인(가로) 구성 표
    if v is not None:
        return v
    return 0.0 if zero else None


def _stfi_note(soup, target):
    """기타금융자산에 묶인 단기금융상품"""
    return _comp(soup, target, STFI_N)


def _expand(tr):
    out = []
    for c in tr.find_all(["td", "th"]):
        try:
            n = max(1, int(c.get("colspan") or 1))
        except ValueError:
            n = 1
        out += [c.get_text(" ", strip=True)] * n
    return out


def _comp_t(soup, target, item_rx):
    """'구분 | 장단기금융상품 | 파생상품자산 | … | 합계' 머리글에 '유동' 행이 있는 가로 표: 합계 칸이 target 인 행의 해당 항목 칸"""
    for t in soup.find_all("table"):
        if t.find_parent("table") is not None:
            continue
        trs = t.find_all("tr")
        if len(trs) < 2:
            continue
        top = cols = tot = None
        for tr in trs[:4]:  # 항목 이름이 든 머리글 행
            h = [re.sub(r"주\d+$", "", _label(x)) for x in _expand(tr)]
            if any(item_rx.search(x) for x in h):
                top = h
                break
        if not top:
            continue
        groups = {}
        for i, h in enumerate(top):
            groups.setdefault(h, []).append(i)
        cols = [g for h, g in groups.items() if item_rx.search(h)]
        tot = [i for h, g in groups.items() if re.search(r"합계|^계$", h) for i in g]
        if not tot:
            continue
        u = _unit_of(t)
        for tr in trs[1:]:
            cells = _expand(tr)
            if len(cells) != len(top):
                continue
            nums = [audit_fs._num(c) for c in cells]
            if any(nums[k] is not None and abs(nums[k] / u - target) <= max(0.05, abs(target) * 1e-4) for k in tot):
                # 항목마다 칸이 여럿(총장부금액 · 할인차금 · 손상 · 장부금액)이면 첫 숫자 칸
                return sum(next((nums[k] for k in g if nums[k] is not None), 0) for g in cols) / u
    return None


PPE_W = re.compile(r"유형자산|투자부동산|무형자산|건설중인자산")
INV_W = re.compile(r"재고자산|용지|미완성")
CAP_SENT = re.compile(r"(?:유형자산|투자부동산|무형자산)[^.。]{0,40}?자본화(?:된|한)\s*차입원가는?\s*([\d,]+)\s*(백만원|천원|원)"
                      r"(?:\s*\(\s*전기\s*[:：]?\s*([\d,]+)\s*(백만원|천원|원)?\s*\))?")
_UW = {"원": 1e8, "천원": 1e5, "백만원": 100}


def _cap_item(soup):
    """자본화된 차입원가(유형자산 · 투자부동산 · 무형자산 · 재고자산) → [당기, 전기](억원).
    자산 종류별 표가 따로 있으면 더하고, 표가 없으면 '유형자산에 대해 자본화된 차입원가는 18,932백만원
    (전기: 4,259백만원)' 같은 문장에서 읽는다"""
    found = {}
    for t in soup.find_all("table"):
        if t.find_parent("table") is not None:
            continue
        if not any(CAPI.match(_label(c.get_text(" ", strip=True))) for c in t.find_all(["td", "th"])):
            continue
        ctx = ""
        p = t
        for _ in range(6):  # 표 바로 앞 문단(주석 제목 등)
            p = p.find_previous(string=re.compile(r"\S")) if p else None
            if p is None:
                break
            if p.find_parent("table") is t:
                continue
            ctx = _sp(str(p)) + ctx
            if len(ctx) > 150:
                break
        ctx = ctx[-150:] + _sp(t.get_text(" ", strip=True))[:120]
        ip = max((m.end() for m in PPE_W.finditer(ctx)), default=-1)
        ii = max((m.end() for m in INV_W.finditer(ctx)), default=-1)
        kind = "inv" if ii > ip else "ppe" if ip >= 0 else "gen"
        r = _item_row(t, CAPI)
        if r and len(found.setdefault(kind, [])) < 2:
            found[kind].append(r)
    if found:
        kinds = [k for k in ("ppe", "inv") if k in found] or ["gen"]
        vals = [_pick2(found[k]) for k in kinds]
        v0 = sum(v[0] for v in vals if v[0] is not None)
        v1s = [v[1] for v in vals if v[1] is not None]
        if "ppe" in found:
            return [v0, sum(v1s) if v1s else None]
    else:
        v0, v1s = None, []
    txt = _sp(soup.get_text(" "))
    s0 = s1 = None
    for m in CAP_SENT.finditer(txt.replace(" ", "")):
        a = audit_fs._num(m.group(1))
        if a:
            s0 = (s0 or 0) + a / _UW[m.group(2)]
        if m.group(3):
            b = audit_fs._num(m.group(3))
            if b is not None:
                s1 = (s1 or 0) + b / _UW[m.group(4) or m.group(2)]
    if s0 is None:
        return [v0, sum(v1s) if v1s else None]
    return [(v0 or 0) + s0, ((sum(v1s) if v1s else 0) + s1) if s1 is not None else (sum(v1s) if v1s else None)]


def _item_row(t, rx):
    """표 하나에서 rx 항목 행 → (당기, 전기 또는 None, XBRL 표 여부) 또는 None"""
    trs = _trs(t)
    if len(trs) < 2:
        return None
    hs = [_sp(c.get_text()) for c in trs[0].find_all(["th", "td"])]
    nper = sum(1 for h in hs[1:] if PERIOD_RX.search(h) and "주석" not in h)
    u = _unit_of(t)
    for tr in trs[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        k0 = next((i for i, c in enumerate(cells) if audit_fs._num(c) is not None), len(cells))
        labs = [c for c in cells[:k0] if _sp(c)]
        if not labs or not rx.match(_label(labs[-1])):
            continue
        nums = [x for x in (audit_fs._num(v) for v in cells[k0:]) if x is not None]
        if not nums:
            continue
        if nper < 2 and any("합계" in h for h in hs[1:]):  # '재고자산 | 유형자산 | 합계' 처럼 나눈 표는 합계 칸
            nums = nums[-1:]
        v0 = abs(nums[0]) / u
        v1 = abs(nums[1]) / u if nper >= 2 and len(nums) >= 2 else None
        return (v0, v1, "공시금액" in "".join(hs) or nper == 0)
    return None


def _pick2(found):
    """[(당기, 전기, XBRL)] → [당기, 전기] — XBRL 표는 당기 표 다음 표가 전기"""
    if not found:
        return [None, None]
    v0, v1, xb = found[0]
    if v1 is None and xb and len(found) > 1:
        v1 = found[1][0]
    return [v0, v1]


def _note_item(soup, rx):
    """주석 표에서 rx 항목 값 → [당기, 전기](억원). 처음 나온 표 기준"""
    found = []
    for t in soup.find_all("table"):
        if t.find_parent("table") is None:
            r = _item_row(t, rx)
            if r:
                found.append(r)
                if len(found) > 1:
                    break
    return _pick2(found)


def _cur_head(hs):
    """표 머리글에 당기(말) 칸이 있음 — 공시금액(XBRL) · 당기 · 제N(당)기 · 날짜(2021.12.31)"""
    return any("공시금액" in h or re.search(r"^당기|당기말|^제\d+\(당\)기|^당\(?반?기|^당분기|^\d{4}[.\-년]\s?\d{1,2}", h) for h in hs[1:])


def is_da(l):
    return bool(DA.match(l)) or (l.startswith("감가상각비") and bool(re.fullmatch(r"감가상각비[,및와]?[가-힣,및와]*상각비", l))
                                  and not re.search(r"환입|손상|누계|채권|대손|처분", l))


def _flow_vals(rows, u, is_cf=True, stmt=False):
    np_ = max(len(p) for _, p in rows)
    if not is_cf and np_ > 2 and not stmt:  # 범주 · 부문별로 칸이 많은 표(첫 칸이 당기가 아님)는 이자비용도 읽지 않는다
        return [{} for _ in range(np_)]
    da = [None] * np_; it = [None] * np_
    for lab, per in rows:
        tgt = da if (is_da(lab) and is_cf) else it if INT.match(lab) else None
        if tgt is None or (tgt is it and any(x is not None for x in it)):
            continue
        for i, v in enumerate(per):
            if v is not None:
                tgt[i] = (tgt[i] or 0) + abs(v)
    r = lambda v: None if v is None else round(v / u, 1)
    return [{**({"da": r(da[i])} if da[i] else {}), **({"int": r(it[i])} if it[i] else {})} for i in range(np_)]


CASH_NOTE = re.compile(r"영업.{0,8}창출|영업활동.{0,6}현금|현금흐름표|조정\s*내역|수익.{0,4}비용.{0,4}조정")
FIN_NOTE = re.compile(r"금융원가|금융비용|금융수익")


def _good_da(rows):
    """감가상각비가 든 현금흐름 조정 표인지(무형자산상각비 · 당기순이익 · 법인세 행이 함께 있음) — 리스 · 부문 주석 표와 구분"""
    labs = [l for l, _ in rows]
    return any(is_da(l) for l in labs) and any(re.search(r"^무형자산상각비|당기순이익|법인세|이자비용|퇴직급여", l) for l in labs if not is_da(l) or l.startswith("무형"))


def extract(rcept_no, cons, log=print):
    """원문 한 건 → {"v", "periods": [당기, 전기, 전전기] 각 {debt, cash, da, int}} — 같은 기준(연결/별도)만"""
    nodes = audit_fs._nodes(rcept_no)
    st, notes = _pick_nodes(nodes, cons)
    per = [{}, {}, {}]
    got = lambda k: any(k in e for e in per[:1])
    bs_done = fin_liab = lease_row = False
    ofa = [None, None, None]
    ofl = [[], [], []]  # 리스부채가 묶일 수 있는 기타금융부채 행(기간별)
    cand_da, cand_da2, cand_int_st, cand_int_nt = [], [], [], []
    lease_c = [{}, {}]; arl_v = cap_v = None

    def items(soup):  # 금융비용에 더하는 매출채권처분손실 · 자본화된 차입원가(처음 나온 표)
        nonlocal arl_v, cap_v
        if arl_v is None:
            x = _note_item(soup, ARL)
            arl_v = x if x[0] is not None else None
        if cap_v is None:
            x = _cap_item(soup)
            cap_v = x if x[0] is not None else None

    def take(fv, keys):
        for i, e in enumerate(fv[:3]):
            for k in keys:
                if k in e and k not in per[i]:
                    per[i][k] = e[k]

    def collect_flows(flows, nm, note):
        for rows, u, ctx, is_cf, hs in flows:
            cf = is_cf or bool(re.search(r"현금흐름|창출", nm))
            # 손익계산서(머리글이 모두 기간)는 3개년 칸이어도 이자비용을 읽는다
            stmt = bool(re.search(r"손익계산서$|^\d+\.(?:연결)?재무제표$|^\(첨부\)(?:연결)?재무제표$", nm)) and all(
                PERIOD_RX.search(h) for h in hs[1:] if h)
            fv = _flow_vals(rows, u, cf, stmt)
            if cf and any("da" in e for e in fv):
                if re.search(r"토지|건물|기계|차량|부동산|구축물|비품|합계$", "".join(hs[1:])) and not any(
                        re.search(r"당기순이익|법인세", l) for l, _ in rows):
                    pass  # 자산 종류별로 나눈 리스 · 유형자산 주석 표(사용권자산 감가상각비 등)는 쓰지 않는다
                else:
                    full = any(re.search(r"당기순이익|법인세비용|영업활동|영업에서창출", l) for l, _ in rows)
                    (cand_da if _good_da(rows) else cand_da2).append((0 if full else 1, len(cand_da) + len(cand_da2), fv))
            if any("int" in e and e["int"] for e in fv):
                if note and not cf and re.search(r"리스|확정급여|퇴직|사용권|충당|기초|기말|부채", "".join(hs[1:])):
                    continue  # 리스부채 · 확정급여채무 변동 표의 이자비용(이자원가)은 금융비용이 아님
                fin = any(re.search(r"^금융(?:비용|원가)", l) for l, _ in rows) or bool(FIN_NOTE.search(ctx))
                (cand_int_nt if (note and not stmt) else cand_int_st).append((fv, cf, ctx, 0 if (cf or fin or stmt) else 1))

    for n in st:
        soup = _html(rcept_no, n)
        time.sleep(0.2)
        bs, flows = _scan_tables(soup)
        if bs and not bs_done:
            for i, e in enumerate(_bs_vals(*bs[0])[:3]):
                lease_row = e.pop("_lease_row")
                if "_ofa" in e:
                    ofa[i] = e.pop("_ofa")
                ofl[i] = e.pop("_ofl")
                per[i].update(e)
            bs_done = True
            fin_liab = any(FIN_LIAB.match(l) for l, _ in bs[0][0])
        collect_flows(flows, _sp(n["text"]), False)
        items(soup)
    debt_note = bs_done and (not got("debt") or fin_liab)  # 재무상태표를 못 찾은 원문은 차입금을 주석에서 추정하지 않는다
    need_lease = bs_done and not lease_row and (got("debt") or debt_note)
    need_stfi = ofa[0] is not None and "cash" in per[0]
    lease_v = stfi_v = None
    if notes:  # 주석은 항상 읽는다(매출채권처분손실 · 자본화된 차입원가 · 리스부채 · 단기금융상품 구성)
        for n in notes:
            nm = _sp(n["text"])
            if not nm.endswith("주석") and not re.search(r"현금흐름|영업활동|창출|금융수익|금융원가|금융비용|차입금|사채|리스|금융자산|금융상품|손익계산서", nm):
                continue
            soup = _html(rcept_no, n)
            time.sleep(0.2)
            _, flows = _scan_tables(soup, want_bs=False)
            items(soup)
            if need_lease:
                for i in range(2):  # 기타금융부채 구성 표의 리스부채(재무상태표 값과 맞는 표)
                    for j, tv in enumerate(ofl[i]):
                        if j not in lease_c[i]:
                            r = _comp(soup, tv, LEASE_ANY)
                            if r is not None:
                                lease_c[i][j] = r
                if lease_v is None and (nm.endswith("주석") or "리스" in nm):
                    lv = _lease_note(soup)
                    if lv[0] is not None:
                        lease_v = lv
            if need_stfi:
                stfi_v = stfi_v or [None, None]
                for i in range(2):
                    if stfi_v[i] is None:
                        stfi_v[i] = _stfi_note(soup, ofa[i])
            if debt_note:
                # 재무상태표에 차입금 행이 없는 회사(단기 · 장기금융부채로만 표시): 주석의 차입금 · 사채 · 리스부채 구성 표에서
                # 항목별로 처음 나온 당기 값. 만기 분석처럼 기간 머리글이 없는 표는 쓰지 않는다.
                seen = {}
                for t in soup.find_all("table"):
                    if t.find_parent("table") is not None:
                        continue
                    trs = _trs(t)
                    if not trs or len(trs) > 16:
                        continue
                    hs = [_sp(c.get_text()) for c in trs[0].find_all(["th", "td"])]
                    if not _cur_head(hs):
                        continue
                    rows, np_ = _rows(t)
                    if np_ > 2:
                        continue
                    u = _unit_of(t)
                    for l, pv in rows:
                        if DEBT_STRICT.match(l) and l not in seen and pv and pv[0] is not None:
                            seen[l] = pv[0] / u
                has_b = lambda d: any(re.search(r"차입금|사채|장기부채", l) for l in d)  # 리스부채만 있는 표는 차입금 표가 아님
                if len(seen) < 2 or not has_b(seen):
                    seen = {}
                    # 구성 표가 없으면 재무활동 부채 변동 표(기초 · 증감 · 기말)의 기말 칸 — 당기 표가 먼저 나온다
                    for t in soup.find_all("table"):
                        if t.find_parent("table") is not None:
                            continue
                        trs = _trs(t)
                        if not trs:
                            continue
                        hs = [_sp(c.get_text()) for c in trs[0].find_all(["th", "td"])]
                        if not (any(h.startswith("기초") for h in hs) and any(h.startswith("기말") for h in hs)):
                            continue
                        u = _unit_of(t)
                        got_ = {}
                        for tr in trs[1:]:
                            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                            if len(cells) < 3:
                                continue
                            l = _label(cells[0])
                            nums = [audit_fs._num(v) for v in cells[1:]]
                            if DEBT_STRICT.match(l) and nums and nums[-1] is not None and l not in got_:
                                got_[l] = nums[-1] / u
                        if len(got_) >= 2 and has_b(got_):
                            seen = got_
                            break
                GEN = {"차입금", "장기부채", "사채및차입금", "차입금및사채"}  # 합계 행 — 세부 항목이 있으면 빼야 이중으로 더하지 않는다
                if any(l not in GEN and re.search(r"차입금|사채", l) for l in seen):
                    seen = {l: v for l, v in seen.items() if l not in GEN}
                if len(seen) >= 2:
                    if not any("리스" in l for l in seen):  # 재무상태표에 따로 있는 리스부채 행은 더한다
                        seen["_bs_lease"] = per[0].get("_bsl", 0.0)
                    per[0]["debt"] = round(sum(seen.values()), 1)
                    debt_note = False
            collect_flows(flows, nm, True)
    cand_int_st.sort(key=lambda x: x[3]); cand_int_nt.sort(key=lambda x: x[3])
    # 현금흐름 조정 표(당기순이익 · 법인세 행이 함께 있는 표)를 먼저, 같은 등급이면 먼저 나온 표
    best = min(cand_da, key=lambda x: x[:2])[2] if cand_da else (min(cand_da2, key=lambda x: x[:2])[2] if cand_da2 else None)
    if best:
        take(best, ("da", "int"))
    if not got("int"):
        for fv, cf, ctx, _r in cand_int_st + cand_int_nt:
            take(fv, ("int",))
            if got("int"):
                break
    lb = per[0].get("_liab") or 0
    if bs_done and not fin_liab and "debt" not in per[0] and (per[0].get("int") or 0) <= max(1, lb * 0.005):
        # 재무상태표 · 주석 어디에도 차입금이 없고 이자비용도 부채총계의 0.5% 이하(리스 · 보증금 이자 수준): 무차입
        # (리스부채는 아래에서 더함)
        for e in per:
            if "cash" in e:
                e["debt"] = 0.0
    if fin_liab:  # 차입금을 금융부채로 묶은 재무상태표: 재무상태표만으로는 일부(리스부채 등)만 잡히므로
        for e in per[1:]:  # 주석에서 읽는 당기 외의 기간은 쓰지 않는다
            e.pop("debt", None)
        if debt_note:  # 주석에서도 차입금 구성을 못 찾음
            per[0].pop("debt", None)
    if need_lease:
        # 재무상태표에 리스부채 행이 없음: 기타금융부채 구성 표의 리스부채(장부금액)를 먼저, 없으면 리스 주석의 합계
        lc = [sum(lease_c[i].values()) if lease_c[i] else None for i in range(2)]
        if lc[0]:
            lease_v = [lc[0], lc[1] if lc[1] else None]
    if lease_v is not None:
        for i in range(3):
            if "debt" in per[i]:
                if i < 2 and lease_v[i] is not None:
                    per[i]["debt"] = round(per[i]["debt"] + lease_v[i], 1)
                else:
                    per[i].pop("debt")
    if need_stfi:  # 기타금융자산에 묶인 단기금융상품
        for i in range(3):
            # 구성 주석을 못 찾으면 현금성자산은 재무상태표 행만으로 둔다(빈칸보다 낫고, 순차입금이 다소 크게 잡힘)
            if ofa[i] is not None and "cash" in per[i] and stfi_v is not None and i < 2 and stfi_v[i] is not None:
                per[i]["cash"] = round(per[i]["cash"] + stfi_v[i], 1)
    for i in range(2):  # 금융비용 = 이자비용 + 매출채권처분손실 + 자본화된 차입원가
        for k, x in (("arl", arl_v), ("cap", cap_v)):
            if x and x[i] is not None and x[i] > 0:
                per[i][k] = round(x[i], 1)
    for e in per:
        e.pop("_bsl", None)
        e.pop("_liab", None)
    return {"v": VERSION, "periods": per}
