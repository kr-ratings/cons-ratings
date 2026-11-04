"""주요 재무지표용 추가 항목 — DART 공시뷰어 원문(키 불필요)에서 읽는다.

재무 API(주요계정)에는 차입금 · 현금성자산 · 감가상각비 · 이자비용이 없어서, 사업 · 반기 · 분기보고서나
감사보고서 원문의 재무상태표 · 현금흐름표와 주석에서 같은 기준(연결 또는 별도)의 숫자를 읽는다.

  debt  총차입금 = 단기차입금 + 유동성장기차입금 · 유동성사채 + 사채 + 장기차입금 + 리스부채(유동 · 비유동) 등
  cash  현금성자산 = 현금및현금성자산 + 단기금융상품 · 단기금융자산 · 단기투자자산 · 단기예금
  da    감가상각비 + 무형자산상각비(사용권자산 · 투자부동산 상각 포함) — 현금흐름표 조정 항목, 없으면 '영업에서 창출된 현금' 주석
  int   이자비용 — 같은 표, 없으면 금융원가(금융비용) 주석
금액은 억원. 찾지 못한 항목은 넣지 않는다(화면에는 '–').
"""
import re, time

from bs4 import BeautifulSoup

import audit_fs

VERSION = 5
_sp = audit_fs._sp

DEBT = re.compile(r"차입금|사채|리스부채|유동화채무|유동성장기부채|^장기부채$")  # '유동차입금(사채포함)', '단기차입금및유동성차입금' 같은 이름도
# 주석 표에서는 이름이 정확히 맞는 항목만(합계 · 소계 행과 겹치지 않게)
DEBT_STRICT = re.compile(r"^(?:유동성|비유동성?|유동|단기|장기)?(?:장기)?(?:차입금|사채|전환사채|교환사채|신주인수권부사채|리스부채|유동화채무|장기부채)$|^유동성장기부채$")
FIN_LIAB = re.compile(r"^(?:단기|장기|유동|비유동)금융부채$")  # 차입금을 금융부채로 묶어 표시한 재무상태표 → 주석에서 차입금 구성을 읽는다
DEBT_NOT = re.compile(r"할인|할증|조정|상환권|전환권|미지급|이자|보증|충당|대여|채권|자산|미수|선급|발행비|파생|합계|총계|소계|공사채무|청구|선수|예수|임대")
CASH = re.compile(r"^현금및현금성자산$|^현금및예치금$")
STFI = re.compile(r"^(?:단기금융상품|단기금융자산|단기투자자산|단기예금|단기예치금|단기투자증권)$")
DA = re.compile(r"^(?:유형자산|투자부동산|사용권자산)?감가상각비$|^(?:무형자산|사용권자산)?상각비$|^감가상각비및(?:무형자산)?상각비$|^감가상각비와(?:무형자산)?상각비$")
INT = re.compile(r"^이자비용$")
PERIOD_RX = audit_fs.PERIOD_RX


def _label(s):
    l = audit_fs._label(re.sub(r"^[\s\-–·ㆍ※▶]+", "", s or ""))
    l = re.sub(r"\(주석?[\d,\.\s]*\)?|\(\*\d*\)|\*\d*$|<[^>]*>", "", l)
    l = re.sub(r"[\(\)（）]", "", l)
    return re.sub(r"(?:에대한)?조정(?:액)?$|가산$", "", l)  # 현금흐름 조정 표의 '감가상각비에 대한 조정' → 감가상각비


def _rows(t):
    """표 하나 → [(항목, [기간별 값])] — 머리글의 기간 수로 열을 나누고, 기간마다 첫 숫자 칸을 쓴다.
    분기 · 반기 표처럼 '3개월 · 누적' 칸이 있으면 누적 칸을 쓴다."""
    trs = t.find_all("tr")
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
            flows.append((rows, u, ctx, is_cf))
    return bs, flows


def _bs_vals(rows, u):
    debt, cash, stfi = None, None, None
    np_ = max(len(p) for _, p in rows)
    D = [0.0] * np_; C = [None] * np_; S = [0.0] * np_
    seen_debt = False
    for lab, per in rows:
        if DEBT.search(lab) and not DEBT_NOT.search(lab):
            seen_debt = True
            for i, v in enumerate(per):
                if v is not None:
                    D[i] += v
        elif CASH.match(lab) and C[0] is None:
            C = [v for v in per] + [None] * (np_ - len(per))
        elif STFI.match(lab):
            for i, v in enumerate(per):
                if v is not None:
                    S[i] += v
    r = lambda v: None if v is None else round(v / u, 1)
    out = []
    for i in range(np_):
        e = {}
        if seen_debt:
            e["debt"] = r(D[i])
        if C[i] is not None:
            e["cash"] = r(C[i] + S[i])
        out.append(e)
    return out


def is_da(l):
    return bool(DA.match(l)) or (l.startswith("감가상각비") and bool(re.fullmatch(r"감가상각비[,및와]?[가-힣,및와]*상각비", l))
                                  and not re.search(r"환입|손상|누계|채권|대손|처분", l))


def _flow_vals(rows, u, is_cf=True):
    np_ = max(len(p) for _, p in rows)
    if not is_cf and np_ > 2:  # 범주 · 부문별로 칸이 많은 표(첫 칸이 당기가 아님)는 이자비용도 읽지 않는다
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
    return [{**({"da": r(da[i])} if da[i] else {}), **({"int": r(it[i])} if it[i] is not None else {})} for i in range(np_)]


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
    bs_done = fin_liab = False
    cand_da, cand_da2, cand_int_st, cand_int_nt = [], [], [], []

    def take(fv, keys):
        for i, e in enumerate(fv[:3]):
            for k in keys:
                if k in e and k not in per[i]:
                    per[i][k] = e[k]

    def collect_flows(flows, nm, note):
        for rows, u, ctx, is_cf in flows:
            cf = is_cf or bool(re.search(r"현금흐름|창출", nm))
            fv = _flow_vals(rows, u, cf)
            if cf and any("da" in e for e in fv):
                (cand_da if _good_da(rows) else cand_da2).append(fv)
            if any("int" in e and e["int"] for e in fv):
                (cand_int_nt if note else cand_int_st).append((fv, cf, ctx))

    for n in st:
        soup = _html(rcept_no, n)
        time.sleep(0.2)
        bs, flows = _scan_tables(soup)
        if bs and not bs_done:
            for i, e in enumerate(_bs_vals(*bs[0])[:3]):
                per[i].update(e)
            bs_done = True
            fin_liab = any(FIN_LIAB.match(l) for l, _ in bs[0][0])
        collect_flows(flows, _sp(n["text"]), False)
    debt_note = bs_done and (not got("debt") or fin_liab)  # 재무상태표를 못 찾은 원문은 차입금을 주석에서 추정하지 않는다
    if not cand_da or not (cand_int_st or any("int" in e for e in cand_da[0][:1])) or debt_note:
        for n in notes:
            nm = _sp(n["text"])
            if not nm.endswith("주석") and not re.search(r"현금흐름|영업활동|창출|금융수익|금융원가|금융비용|차입금|사채|리스", nm):
                continue
            soup = _html(rcept_no, n)
            time.sleep(0.2)
            _, flows = _scan_tables(soup, want_bs=False)
            if debt_note:
                # 재무상태표에 차입금 행이 없는 회사(단기 · 장기금융부채로만 표시): 주석의 차입금 · 사채 · 리스부채 구성 표에서
                # 항목별로 처음 나온 당기 값. 만기 분석처럼 기간 머리글이 없는 표는 쓰지 않는다.
                seen = {}
                for t in soup.find_all("table"):
                    if t.find_parent("table") is not None:
                        continue
                    trs = t.find_all("tr")
                    if not trs or len(trs) > 16:
                        continue
                    hs = [_sp(c.get_text()) for c in trs[0].find_all(["th", "td"])]
                    if not any("공시금액" in h or re.search(r"^당기|당기말|^제\d+\(당\)기|^당\(?반?기|^당분기", h) for h in hs[1:]):
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
                        trs = t.find_all("tr")
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
                    per[0]["debt"] = round(sum(seen.values()), 1)
                    debt_note = False
            collect_flows(flows, nm, True)
    if fin_liab and debt_note:  # 차입금을 금융부채로 묶은 재무상태표인데 주석에서 차입금 구성을 못 찾음 → 일부만 더한 값은 쓰지 않는다
        for e in per:
            e.pop("debt", None)
    best = (cand_da or cand_da2 or [None])[0]
    if best:
        take(best, ("da", "int"))
    if not got("int"):
        for fv, cf, ctx in cand_int_st + cand_int_nt:
            take(fv, ("int",))
            if got("int"):
                break
    return {"v": VERSION, "periods": per}
