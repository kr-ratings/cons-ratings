"""DART(금융감독원 전자공시) 공시 · 재무 수집 — 환경변수 DART_API_KEY 필요(Open DART 무료 인증키)

1) 고유번호 매핑: corpCode.xml 의 회사명을 시평 회사명과 맞춘다. 같은 이름이 여럿이면 업종(건설업)으로 고른다.
   결과는 collector/dart_ids.json 에 저장해 두고 다음부터 재사용한다(직접 고쳐 넣어도 된다).
2) 공시: 회사별 최근 공시 목록(list.json)에서 실무에 의미 있는 공시만 분류해 최근 90일치를 남긴다.
3) 재무: 다중회사 주요계정(fnlttMultiAcnt)으로 최근 5개 사업연도 + 올해 최신 분기(누적)를 받는다.
   정기보고서를 내지 않는 비상장 외감법인은 구조화된 재무가 없어서 감사보고서 원문 링크만 남긴다.
"""
import io, json, os, re, time, zipfile, datetime as dt
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

API = "https://opendart.fss.or.kr/api/"
IDS_PATH = Path(__file__).resolve().parent / "dart_ids.json"
DISC_DAYS = 90
FIN_REFRESH_DAYS = 7

CATS = [  # (분류, 보고서명 정규식) — 앞에 있는 분류 우선
    ("주요사항", r"부도|영업정지|회생|파산|해산|소송|감자|관리절차|횡령|배임|불성실|채권은행|상장폐지|감사의견|거래정지"),
    ("수주", r"단일판매|공급계약"),
    ("보증·자금", r"채무보증|담보제공|채무인수|자금보충|증권신고서\(채무증권\)|증권발행실적보고서|유상증자|전환사채|신주인수권부사채|교환사채|"
                r"신종자본|사채원리금|단기차입금|기업어음|전자단기사채|자기주식|조건부자본증권"),
    ("지배구조", r"최대주주|합병|분할|영업양수|영업양도|자산양수|자산양도|타법인주식|대표이사|주식교환|공개매수"),
    ("실적", r"영업\(잠정\)실적|매출액또는손익구조|손익구조"),
    ("정기", r"사업보고서|반기보고서|분기보고서|감사보고서"),
]
CATS = [(c, re.compile(p)) for c, p in CATS]
SKIP = re.compile(r"소유상황보고서|대량보유|기업설명회|투자설명서|일괄신고추가서류|효력발생|공정공시.*(IR|설명회)|정정명령|대규모기업집단현황|"
                  r"특수관계인|동일인|비상장회사 중요사항|증권신고서\(지분증권\)의 철회")
ACCT = {"매출액": "rev", "영업이익": "op", "당기순이익": "ni", "자산총계": "assets", "부채총계": "liab", "자본총계": "equity",
        "영업이익(손실)": "op", "당기순이익(손실)": "ni", "수익(매출액)": "rev"}
REPRT = [("11011", "FY"), ("11014", "3Q"), ("11012", "1H"), ("11013", "1Q")]


def acct_key(nm):
    n = re.sub(r"\s", "", nm or "")
    if n in ("자산총계", "부채총계", "자본총계"):
        return {"자산총계": "assets", "부채총계": "liab", "자본총계": "equity"}[n]
    if re.match(r"^(매출액|수익\(매출액\)|영업수익|매출$)", n):
        return "rev"
    if re.match(r"^영업(이익|손실|손익)", n):
        return "op"
    if re.match(r"^(연결)?당기순(이익|손실|손익)", n):
        return "ni"
    return None


def classify(nm):
    if SKIP.search(nm):
        return None
    for c, rx in CATS:
        if rx.search(nm):
            return c
    return None


def _norm(n):
    return re.sub(r"\s|\(주\)|㈜|주식회사|\(|\)", "", str(n or ""))


class Dart:
    def __init__(self, key, log=print):
        self.key, self.log = key, log
        self.s = requests.Session()
        self.calls = 0

    def json(self, ep, **params):
        for i in range(3):
            try:
                self.calls += 1
                r = self.s.get(API + ep, params={"crtfc_key": self.key, **params}, timeout=40)
                r.raise_for_status()
                d = r.json()
                if d.get("status") in ("020",):  # 요청 제한 초과
                    raise RuntimeError("DART 일일 요청 한도 초과")
                return d
            except RuntimeError:
                raise
            except Exception:
                if i == 2:
                    raise
                time.sleep(2 * (i + 1))

    def corp_codes(self):
        r = self.s.get(API + "corpCode.xml", params={"crtfc_key": self.key}, timeout=120)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        root = ET.fromstring(z.read(z.namelist()[0]))
        out = []
        for e in root.iter("list"):
            out.append({k: (e.findtext(k) or "").strip() for k in ("corp_code", "corp_name", "stock_code", "modify_date")})
        return out


def map_ids(D, rows, ids):
    """시평 회사 → DART 고유번호. 이미 있는 매핑은 유지, 없는 회사만 새로 찾는다."""
    need = [r for r in rows if (r.get("reg") or r["name"]) not in ids]
    if not need:
        return ids, []
    allc = D.corp_codes()
    by = {}
    for c in allc:
        by.setdefault(_norm(c["corp_name"]), []).append(c)
    names = json.loads((Path(__file__).resolve().parent / "news_names.json").read_text("utf-8"))
    miss = []
    for r in need:
        # 시평 엑셀의 한글 표기(지에스건설)와 DART 표기(GS건설)가 다른 경우가 많아 기사 검색용 이름도 후보로 쓴다
        keys = [_norm(r["name"])] + [_norm(q) for q in (names.get(_norm(r["name"])) or {}).get("q", [])]
        cands, seen = [], set()
        for k in keys:
            for c in by.get(k, []):
                if c["corp_code"] not in seen:
                    seen.add(c["corp_code"]); cands.append(c)
        pick = None
        if len(cands) == 1:
            pick = cands[0]
        elif cands:
            scored = []
            # 같은 이름이 여럿이면 아래 score 순으로 고른다
            since2 = (dt.date.today() - dt.timedelta(days=730)).strftime("%Y%m%d")
            for c in sorted(cands, key=lambda c: (bool(c["stock_code"]), c["modify_date"]), reverse=True)[:20]:
                try:
                    info = D.json("company.json", corp_code=c["corp_code"])
                    lst = D.json("list.json", corp_code=c["corp_code"], bgn_de=since2, page_count=1)
                except Exception:
                    continue
                ind = str(info.get("induty_code") or "")
                n = int(lst.get("total_count") or 0) if lst.get("status") == "000" else 0
                region = (r.get("region") or "")[:2]
                same_region = bool(region) and str(info.get("adres") or "").replace("특별시", "").replace("광역시", "")[:2] == region
                # 살아 있는 회사 → 시평 소재지(시도)와 본점 주소 일치 → 공시 건수 많은 순 → 건설업
                score = (n > 0, same_region, n, ind[:2] in ("41", "42"), bool(c["stock_code"]), c["modify_date"])
                scored.append((score, c))
                time.sleep(0.1)
            if scored:
                pick = max(scored, key=lambda x: x[0])[1]
        if pick:
            ids[r.get("reg") or r["name"]] = {"name": r["name"], "corp_code": pick["corp_code"], "dart_name": pick["corp_name"],
                                              "stock": pick["stock_code"] or None, "cands": len(cands)}
        else:
            miss.append(r["name"])
    return ids, miss


def disclosures(D, corp_code, since):
    out, page = [], 1
    while True:
        d = D.json("list.json", corp_code=corp_code, bgn_de=since.replace("-", ""), page_no=page, page_count=100)
        if d.get("status") == "013":  # 조회된 데이터 없음
            break
        if d.get("status") != "000":
            raise RuntimeError(f"list.json {d.get('status')} {d.get('message')}")
        for x in d.get("list", []):
            nm = re.sub(r"\s+", " ", x.get("report_nm", "")).strip()
            c = classify(nm)
            if not c or (nm.startswith("[기재정정]") and c in ("수주", "정기")):
                continue  # 공급계약 진행률 정정 등은 너무 잦아서 제외
            rd = x.get("rcept_dt", "")
            out.append({"d": f"{rd[:4]}-{rd[4:6]}-{rd[6:8]}", "nm": nm, "cat": c, "no": x.get("rcept_no"),
                        "flr": x.get("flr_nm"), "rm": x.get("rm") or None})
        if page >= int(d.get("total_page") or 1):
            break
        page += 1
    return out


def _amt(v):
    v = str(v or "").replace(",", "").strip()
    try:
        return round(int(v) / 1e8)  # 원 → 억원
    except ValueError:
        return None


def financials(D, codes, today):
    """{corp_code: {"basis": "연결"/"별도", "periods": [...], "alt": 별도 시리즈(연결이 기본일 때)}} — 다중회사 주요계정, 100개씩

    연결(CFS)과 별도(OFS)를 각각 따로 모은다. 한 시리즈 안에는 한 기준만 들어간다(연도별로 섞지 않음).
    기본 시리즈는 최근 사업연도에 연결이 있으면 연결, 없으면 별도. 연결이 없던 옛 연도는 연결 시리즈에서 빠진다.
    """
    year = today.year
    res = {"CFS": {}, "OFS": {}}

    def take(lst, bsns_year, label):
        by = {}
        for x in lst:
            by.setdefault(x["corp_code"], []).append(x)
        for cc, xs0 in by.items():
            for fs in ("CFS", "OFS"):
                xs = [x for x in xs0 if x.get("fs_div") == fs]
                if not xs:
                    continue
                cur, prv, pp = {}, {}, {}
                for x in xs:
                    k = acct_key(x.get("account_nm", ""))
                    if not k or k in cur:
                        continue
                    # 분기·반기 손익은 thstrm_amount 가 3개월치라서 누적(thstrm_add_amount)을 쓴다
                    cur[k] = _amt(x.get("thstrm_add_amount")) if (label != "FY" and k in ("rev", "op", "ni") and x.get("thstrm_add_amount")) else _amt(x.get("thstrm_amount"))
                    prv[k] = _amt(x.get("frmtrm_amount"))
                    pp[k] = _amt(x.get("bfefrmtrm_amount"))
                ent = res[fs].setdefault(cc, {"basis": "연결" if fs == "CFS" else "별도", "periods": []})
                if label == "FY":
                    have = {p["p"]: p for p in ent["periods"]}
                    for y, vals in ((bsns_year, cur), (bsns_year - 1, prv), (bsns_year - 2, pp)):
                        if f"{y}" in have:  # 이미 있는 연도는 빈 항목만 다른 보고서 값으로 채운다
                            for k, v in vals.items():
                                if have[f"{y}"].get(k) is None and v is not None:
                                    have[f"{y}"][k] = v
                        elif any(v is not None for v in vals.values()):
                            ent["periods"].append({"p": f"{y}", "t": "FY", **vals})
                    ent.setdefault("fy_rcept", xs[0].get("rcept_no"))
                elif not any(p["t"] != "FY" for p in ent["periods"]):
                    if any(v is not None for v in cur.values()):
                        ent["periods"].insert(0, {"p": f"{bsns_year} {label}", "t": label, **cur})
                        ent["q_rcept"] = xs[0].get("rcept_no")

    F6 = ("rev", "op", "ni", "assets", "liab", "equity")
    nfy = lambda fs, c: sum(1 for p in res[fs].get(c, {}).get("periods", []) if p["t"] == "FY" and all(p.get(k) is not None for k in F6))
    chunks = [codes[i:i + 90] for i in range(0, len(codes), 90)]
    # 최근 사업연도: 올해 3월 이후면 작년, 아니면 재작년부터 시도
    for ch in chunks:
        pending, latest = set(ch), {}
        for y in (year - 1, year - 2):
            if not pending:
                break
            d = D.json("fnlttMultiAcnt.json", corp_code=",".join(sorted(pending)), bsns_year=y, reprt_code="11011")
            if d.get("status") == "000":
                lst = d.get("list", [])
                take(lst, y, "FY")
                got = {x["corp_code"] for x in lst}
                latest.update({c: y for c in got})
                pending -= got
        # 5개 사업연도: 사업보고서 한 건에 3개년이 들어 있으므로 2년 전(없으면 1년·3년 전) 사업보고서로 나머지를 채운다
        for off in (2, 1, 3):
            for y in sorted(set(latest.values())):
                cs = sorted(c for c, v in latest.items() if v == y and min(nfy(fs, c) for fs in ("CFS", "OFS") if c in res[fs]) < 5)
                if not cs:
                    continue
                d = D.json("fnlttMultiAcnt.json", corp_code=",".join(cs), bsns_year=y - off, reprt_code="11011")
                if d.get("status") == "000":
                    take(d.get("list", []), y - off, "FY")
        # 올해 최신 분기(3Q → 1H → 1Q)
        pending = set(ch)
        for code, label in REPRT[1:]:
            if not pending:
                break
            d = D.json("fnlttMultiAcnt.json", corp_code=",".join(sorted(pending)), bsns_year=year, reprt_code=code)
            if d.get("status") == "000":
                lst = d.get("list", [])
                take(lst, year, label)
                pending -= {x["corp_code"] for x in lst}
    for fs in ("CFS", "OFS"):
        for ent in res[fs].values():
            for p in ent["periods"]:  # 자산 = 부채 + 자본 으로 빠진 한 항목 보완
                a, l, e = p.get("assets"), p.get("liab"), p.get("equity")
                if e is None and a is not None and l is not None:
                    p["equity"] = a - l
                elif l is None and a is not None and e is not None:
                    p["liab"] = a - e
            fy = sorted((p for p in ent["periods"] if p["t"] == "FY"), key=lambda p: p["p"], reverse=True)[:5]
            ent["periods"] = [p for p in ent["periods"] if p["t"] != "FY"] + fy
            for p in ent["periods"]:
                ratios(p)
    out = {}
    for cc in set(res["CFS"]) | set(res["OFS"]):
        c, o = res["CFS"].get(cc), res["OFS"].get(cc)
        lastfy = lambda e: max((p["p"] for p in e["periods"] if p["t"] == "FY"), default="") if e else ""
        if c and lastfy(c) >= lastfy(o):  # 최근 사업연도에 연결이 있으면 연결이 기본
            out[cc] = {**c, **({"alt": o} if o else {})}
        else:
            out[cc] = o
    return out


ANNUAL = {"1Q": 4, "1H": 2, "3Q": 4 / 3}  # 분기 누적 EBITDA → 연환산(순차입금/EBITDA)


def ratios(p):
    """부채비율 · 영업이익률과 주요 재무지표(EBITDA, 순차입금, 마진, 커버리지, 차입금의존도)"""
    for k in ("dr", "opm", "ebitda", "nd", "ebitdam", "cov", "nde", "dep"):
        p.pop(k, None)
    if p.get("liab") is not None and p.get("equity"):
        p["dr"] = round(p["liab"] / p["equity"] * 100, 1) if p["equity"] > 0 else None
    if p.get("op") is not None and p.get("rev"):
        p["opm"] = round(p["op"] / p["rev"] * 100, 1)
    if p.get("op") is not None and p.get("da") is not None:
        p["ebitda"] = p["op"] + p["da"]
    if p.get("debt") is not None:
        if p.get("cash") is not None:
            p["nd"] = round(p["debt"] - p["cash"], 1)
        if p.get("assets"):
            p["dep"] = round(p["debt"] / p["assets"] * 100, 1)
    if p.get("ebitda") is not None and p.get("rev"):
        p["ebitdam"] = round(p["ebitda"] / p["rev"] * 100, 1)
    if p.get("ebitda") is not None and p.get("fc"):
        p["cov"] = round(p["ebitda"] / p["fc"], 1)
    if p.get("nd") is not None and p.get("ebitda"):
        e = p["ebitda"] * ANNUAL.get(p.get("t"), 1)
        p["nde"] = round(p["nd"] / e, 1) if e > 0 else None


AUD_RX = re.compile(r"^(?:\[[^\]]*\])?\s*(연결)?감사보고서\s*\((\d{4})\.(\d{2})\)")
BIZ_RX = re.compile(r"^(?:\[[^\]]*\])?\s*사업보고서\s*\((\d{4})\.(\d{2})\)")


def _reports(D, corp_code, since, ty, rx, kind):
    out, page = {}, 1
    while True:
        d = D.json("list.json", corp_code=corp_code, bgn_de=since, pblntf_ty=ty, page_no=page, page_count=100)
        if d.get("status") != "000":
            break
        for x in d.get("list", []):
            m = rx.match(re.sub(r"\s+", " ", x.get("report_nm", "")).strip())
            if not m:
                continue
            c = bool(m.group(1)) if kind == "audit" else None
            y = int(m.group(2 if kind == "audit" else 1))
            key = (kind, c, y)
            if key not in out or x["rcept_no"] > out[key]["no"]:  # 정정본이 있으면 나중 것
                rd = x["rcept_dt"]
                out[key] = {"no": x["rcept_no"], "d": f"{rd[:4]}-{rd[4:6]}-{rd[6:8]}", "nm": x["report_nm"].strip(), "y": y, "c": c, "kind": kind}
        if page >= int(d.get("total_page") or 1):
            break
        page += 1
    return out


def ext_plan(D, v, today):
    """회사 하나의 재무 시리즈(기본 · 별도)마다 [(시리즈, 기간, 원문 접수번호, 연결 여부, 원문 안 기간 순번)]
    — 사업연도는 그해 보고서(사업보고서 또는 같은 기준 감사보고서)의 당기, 없으면 다음 해 보고서의 전기. 분기는 그 분기보고서."""
    since = (today - dt.timedelta(days=365 * 7)).strftime("%Y%m%d")
    reps = None
    out = []
    for f in (v.get("fin"), v.get("fin_s")):
        if not f:
            continue
        f.pop("xr", None)
        cons = f["basis"] == "연결"
        if reps is None:
            reps = _reports(D, v["corp_code"], since, "F", AUD_RX, "audit")
            reps.update(_reports(D, v["corp_code"], since, "A", BIZ_RX, "biz"))
        by_y = {}
        for r in reps.values():
            ok = (r["kind"] == "audit" and r["c"] == cons) if f.get("src") == "감사보고서" else r["kind"] == "biz"
            if ok and (r["y"] not in by_y or r["no"] > by_y[r["y"]]["no"]):
                by_y[r["y"]] = r
        for p in f["periods"]:
            if p["t"] != "FY":
                if f.get("q_rcept"):
                    out.append((f, p, f["q_rcept"], cons, 0))
                continue
            y = int(p["p"])
            if y in by_y:
                out.append((f, p, by_y[y]["no"], cons, 0))
            elif y + 1 in by_y:
                out.append((f, p, by_y[y + 1]["no"], cons, 1))
            elif y + 2 in by_y:
                out.append((f, p, by_y[y + 2]["no"], cons, 2))
    return out


def _parse_cached(cache, no, cons, log):
    """원문 재무제표 읽기(접수번호별 저장). 사업보고서처럼 한 원문에 연결 · 별도가 함께 있는 경우가 있어 별도 읽기는 's:' 키로 따로 둔다"""
    import audit_fs
    key = no if cons else "s:" + no
    x = cache.get(key)
    if not x or x.get("v") != audit_fs.VERSION:
        try:
            x = cache[key] = audit_fs.parse(no, prefer_cons=cons) or {"v": audit_fs.VERSION}
        except Exception as e:
            log(f"재무제표 읽기 실패 {no}: {e}")
            return None, key
        time.sleep(0.5)
    return x, key


def _audit_series(reps, cons, want, cache, log):
    """한 기준(연결 또는 별도)만으로 연도별 재무 — 같은 기준 감사보고서 → 사업보고서 원문(같은 기준 재무제표가 있을 때만)"""
    order = sorted([r for r in reps.values() if r["kind"] == "audit" and r["c"] == cons], key=lambda r: -r["y"]) + \
            sorted([r for r in reps.values() if r["kind"] == "biz"], key=lambda r: -r["y"])
    periods, partial, keys = {}, {}, []
    full = lambda v: v.get("assets") is not None and (v.get("rev") is not None or v.get("op") is not None)
    for r in order:
        if all(y in periods for y in want):
            break
        if not any(r["y"] - i in want and r["y"] - i not in periods for i in range(3)):
            continue
        x, key = _parse_cached(cache, r["no"], cons, log)
        if not x or bool(x.get("consolidated")) != cons:  # 다른 기준 재무제표는 쓰지 않는다
            continue
        used = False
        for i, vals in enumerate(x.get("periods") or []):
            y = r["y"] - i
            if y not in want or y in periods:
                continue
            ent = {"p": f"{y}", "t": "FY", **{k: vals.get(k) for k in ("rev", "op", "ni", "assets", "liab", "equity", "debt", "cash", "fc", "da")}}
            if full(vals):
                periods[y] = ent; used = True
            elif vals.get("assets") is not None and y not in partial:
                partial[y] = ent; used = True
        if used:
            keys.append(key)
    for y, ent in partial.items():  # 끝까지 손익을 못 찾은 연도는 있는 숫자라도
        periods.setdefault(y, ent)
    ps = [periods[y] for y in sorted(periods, reverse=True)]
    for p in ps:
        ratios(p)
    return ps, keys


def audit_fin(D, corp_code, today, cache, log=print):
    """구조화 재무가 없는 회사: 감사보고서(없으면 사업보고서) 원문에서 최근 5개 사업연도 → (fin, 최신 감사보고서)

    최근 사업연도에 연결재무제표가 있으면 연결이 기본(연결이 없던 옛 연도는 빼서 연도 수가 줄어듦), 별도는 따로 'alt'에 담는다.
    """
    since = (today - dt.timedelta(days=365 * 7)).strftime("%Y%m%d")
    reps = _reports(D, corp_code, since, "F", AUD_RX, "audit")
    reps.update(_reports(D, corp_code, since, "A", BIZ_RX, "biz"))
    if not reps:
        return None, None
    latest_y = max(r["y"] for r in reps.values())
    if any(r["kind"] == "audit" and r["y"] == latest_y for r in reps.values()):
        cons = ("audit", True, latest_y) in reps
    else:  # 최근 연도가 사업보고서뿐이면 그 원문에 연결재무제표가 있는지로 정한다
        biz = reps.get(("biz", None, latest_y))
        x, _ = _parse_cached(cache, biz["no"], True, log) if biz else (None, None)
        cons = bool(x and x.get("consolidated"))
    auds = [r for r in reps.values() if r["kind"] == "audit"]
    latest = max(auds, key=lambda r: (r["y"], r["c"] == cons, r["no"])) if auds else None
    want = [latest_y - i for i in range(5)]
    ps, keys = _audit_series(reps, cons, want, cache, log)
    if not ps:
        return None, latest
    src = latest or max(reps.values(), key=lambda r: r["y"])
    out = {"basis": "연결" if cons else "별도", "src": "감사보고서" if latest else "사업보고서 원문", "periods": ps, "rcepts": keys,
           "audit_no": src["no"], "audit_nm": src["nm"]}
    if cons:  # 별도 시리즈(전환 버튼용)
        ps2, keys2 = _audit_series(reps, False, want, cache, log)
        if ps2:
            sep = [r for r in auds if not r["c"]]
            s2 = max(sep, key=lambda r: (r["y"], r["no"])) if sep else src
            out["alt"] = {"basis": "별도", "src": out["src"], "periods": ps2, "rcepts": keys2, "audit_no": s2["no"], "audit_nm": s2["nm"]}
    return out, latest


REPRT_NM = {"11011": "사업보고서", "11012": "반기보고서", "11013": "1분기보고서", "11014": "3분기보고서"}


def _pct(v):
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except ValueError:
        return None


def api_holders(D, corp_code, today):
    """사업·분기보고서의 최대주주 및 특수관계인 현황 + 자기주식 → sh 또는 None"""
    y = today.year
    for yr, rc in ((y, "11014"), (y, "11012"), (y, "11013"), (y - 1, "11011"), (y - 2, "11011")):
        d = D.json("hyslrSttus.json", corp_code=corp_code, bsns_year=yr, reprt_code=rc)
        if d.get("status") != "000" or not d.get("list"):
            continue
        lst = []
        for x in d["list"]:
            if not re.search(r"보통|의결권있는", re.sub(r"\s", "", x.get("stock_knd") or "")) or (x.get("nm") or "").strip() in ("계", "합계", "-"):
                continue
            v = _pct(x.get("trmend_posesn_stock_qota_rt"))
            if v is None or v <= 0:
                continue
            lst.append({"nm": x["nm"].strip(), "rel": re.sub(r"\s+", " ", x.get("relate") or "").strip() or None, "pct": round(v, 2)})
        if not lst:
            continue
        tre = None
        t = D.json("stockTotqySttus.json", corp_code=corp_code, bsns_year=yr, reprt_code=rc)
        for x in t.get("list", []) if t.get("status") == "000" else []:
            if "보통" in (x.get("se") or ""):
                tot, tr = _pct(x.get("istc_totqy")), _pct(x.get("tesstk_co"))
                if tot and tr is not None:
                    tre = round(tr / tot * 100, 2)
                break
        asof = d["list"][0].get("stlm_dt")
        return {"list": sorted(lst, key=lambda x: -x["pct"]), "treasury": tre, "asof": asof,
                "src": f"{REPRT_NM[rc]} ({yr})", "no": d["list"][0].get("rcept_no")}
    return None


GROUP_RX = re.compile(r"대규모기업집단현황공시\[연1회공시")


def doc_holders(D, corp_code, names, today, cache, log=print):
    """감사보고서 주석 → 대규모기업집단현황공시 순으로 주주 현황 → sh 또는 None"""
    import audit_fs
    since = (today - dt.timedelta(days=550)).strftime("%Y%m%d")
    reps = _reports(D, corp_code, since, "F", AUD_RX, "audit")
    for r in sorted(reps.values(), key=lambda r: (-r["y"], r["c"])):  # 최신 연도, 별도 먼저
        key = "sh4:" + r["no"]
        if key not in cache:
            try:
                cache[key] = audit_fs.shareholders(r["no"]) or []
            except Exception as e:
                log(f"주주 현황 읽기 실패 {r['no']}: {e}")
                continue
            time.sleep(0.4)
        if cache[key]:
            return {"list": cache[key], "asof": f"{r['y']}-12-31", "src": r["nm"], "no": r["no"]}
    d = D.json("list.json", corp_code=corp_code, bgn_de=since, pblntf_ty="J", page_count=100)
    for x in d.get("list", []) if d.get("status") == "000" else []:
        if not GROUP_RX.search(x.get("report_nm", "")):
            continue
        key = "sh4:" + x["rcept_no"]
        if key not in cache:
            try:
                cache[key] = audit_fs.group_holders(x["rcept_no"], names) or []
            except Exception as e:
                log(f"기업집단 주주 읽기 실패 {x['rcept_no']}: {e}")
                cache[key] = []
            time.sleep(0.4)
        if cache[key]:
            rd = x["rcept_dt"]
            return {"list": cache[key], "asof": f"{rd[:4]}-{rd[4:6]}-{rd[6:8]}", "src": "대규모기업집단현황공시", "no": x["rcept_no"]}
        break  # 가장 최근 것 하나만
    return None


def finish_holders(sh):
    """자기주식은 따로, 이름 있는 주주는 지분율 큰 순 8명까지, 나머지는 '기타'로 묶어 합계 100%"""
    tre = sh.get("treasury") or 0
    named = []
    for x in sh["list"]:
        x = dict(x)
        x["nm"] = re.sub(r"\(\*\d*\)|\*\d*$", "", x["nm"]).strip()          # 각주 표시 제거
        if re.fullmatch(r"[가-힣](\s[가-힣]){1,3}", x["nm"]):                  # '고 광 정' → '고광정'
            x["nm"] = x["nm"].replace(" ", "")
        if not x["nm"] or x["nm"] in ("-", "보통주", "우선주", "보퉁주") or not x.get("pct"):
            continue
        n = re.sub(r"\s", "", x["nm"])
        if n == "자기주식":
            tre += x["pct"]
        elif not re.match(r"^(기타(주주|소액주주)?|그외|그외주주)$", n):
            named.append(x)
    merged = {}
    for x in named:  # 같은 주주가 보통주·우선주 등으로 두 줄이면 합친다
        if x["nm"] in merged:
            merged[x["nm"]]["pct"] = round(merged[x["nm"]]["pct"] + x["pct"], 2)
        else:
            merged[x["nm"]] = x
    named = sorted(merged.values(), key=lambda x: -x["pct"])[:8]
    etc = round(100 - sum(x["pct"] for x in named) - tre, 2)
    return {**sh, "list": named, "treasury": round(tre, 2) if tre else None, "etc": etc if etc > 0.005 else None}


def collect(rows, prev, now, log=print):
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        log("DART_API_KEY 없음 — DART 수집 건너뜀")
        return None
    D = Dart(key, log)
    today = now.date()
    errors = []
    ids = json.loads(IDS_PATH.read_text("utf-8")) if IDS_PATH.exists() else {}
    try:
        ids, miss = map_ids(D, rows, ids)
        IDS_PATH.write_text(json.dumps(ids, ensure_ascii=False, indent=1), "utf-8")
        if miss:
            errors.append(f"DART 고유번호 못 찾음 {len(miss)}개사")
    except Exception as e:
        errors.append(f"고유번호 매핑 실패: {e}")
    co_prev = prev.get("co", {})
    co = {}
    since_full = (today - dt.timedelta(days=DISC_DAYS)).isoformat()
    for r in rows:
        k = r.get("reg") or r["name"]
        m = ids.get(k)
        if not m:
            continue
        p = co_prev.get(k, {})
        old = [x for x in p.get("disc", []) if x["d"] >= since_full
               and not (x["nm"].startswith("[기재정정]") and x["cat"] in ("수주", "정기"))]
        since = since_full if not old else (today - dt.timedelta(days=10)).isoformat()
        try:
            new = disclosures(D, m["corp_code"], since)
            seen = {x["no"] for x in new}
            disc = sorted(new + [x for x in old if x["no"] not in seen], key=lambda x: (x["d"], x["no"]), reverse=True)
        except Exception as e:
            errors.append(f"{r['name']} 공시: {str(e)[:60]}")
            disc = old
        co[k] = {"corp_code": m["corp_code"], "stock": m.get("stock"), "disc": disc[:60],
                 **({"fin": p["fin"]} if p.get("fin") else {}), **({"fin_s": p["fin_s"]} if p.get("fin_s") else {}),
                 **({"audit": p["audit"]} if p.get("audit") else {}),
                 **({"info": p["info"]} if p.get("info") else {}), **({"sh": p["sh"]} if p.get("sh") else {})}
        aud = next((x for x in disc if "감사보고서" in x["nm"]), None)
        if aud:
            co[k]["audit"] = {"d": aud["d"], "no": aud["no"], "nm": aud["nm"]}
        time.sleep(0.15)
    # 재무: 7일마다, 또는 새 정기보고서가 접수됐을 때
    last = prev.get("fin_checked")
    new_periodic = any(x["cat"] == "정기" and (not last or x["d"] >= last) for v in co.values() for x in v["disc"])
    if os.environ.get("DART_FIN_FORCE") or not last or new_periodic or (today - dt.date.fromisoformat(last)).days >= FIN_REFRESH_DAYS:
        try:
            fin = financials(D, sorted({v["corp_code"] for v in co.values()}), today)
            for k, v in co.items():
                if v["corp_code"] in fin:
                    v["fin"] = fin[v["corp_code"]]
            fin_checked = today.isoformat()
        except Exception as e:
            errors.append(f"재무: {str(e)[:80]}")
            fin_checked = last
    else:
        fin_checked = last
    # 사업·분기보고서를 내지 않는 회사(비상장 외감법인)는 감사보고서 본문에서 재무를 읽는다(재무 갱신 때만).
    # 한 번 읽은 감사보고서는 접수번호별로 저장해 두고 다시 받지 않는다.
    afs_cache = dict(prev.get("afs") or {})
    if fin_checked == today.isoformat():
        import audit_fs
        for k, v in co.items():
            if v.get("fin") and v["fin"].get("src") not in ("감사보고서", "사업보고서 원문"):
                continue
            try:
                f, aud = audit_fin(D, v["corp_code"], today, afs_cache, log)
                if f:
                    v["fin"] = f
                elif v.get("fin"):
                    pass  # 실패하면 직전 값 유지
                if aud:
                    v["audit"] = aud
            except Exception as e:
                errors.append(f"감사보고서 {k}: {str(e)[:60]}")
        # 재무 API에 빠진 항목(예: 일부 연도 당기순이익)은 그해 사업보고서 원문의 같은 기준(연결/별도) 재무제표에서 채운다
        F6 = ("rev", "op", "ni", "assets", "liab", "equity")
        for k, v in co.items():
            f0 = v.get("fin")
            if not f0 or f0.get("src"):
                continue
            series = [f0] + ([f0["alt"]] if f0.get("alt") else [])
            if not any(p["t"] == "FY" and any(p.get(x) is None for x in F6) for f in series for p in f["periods"]):
                continue
            try:
                biz = _reports(D, v["corp_code"], (today - dt.timedelta(days=365 * 7)).strftime("%Y%m%d"), "A", BIZ_RX, "biz")
                for f in series:
                    cons = f["basis"] == "연결"
                    for p in [p for p in f["periods"] if p["t"] == "FY" and any(p.get(x) is None for x in F6)]:
                        r = biz.get(("biz", None, int(p["p"])))
                        if not r:
                            continue
                        x, key = _parse_cached(afs_cache, r["no"], cons, log)
                        if not x or not x.get("periods") or bool(x.get("consolidated")) != cons:  # 다른 기준은 쓰지 않는다
                            continue
                        for kk in F6:
                            if p.get(kk) is None and x["periods"][0].get(kk) is not None:
                                p[kk] = x["periods"][0][kk]
                        f.setdefault("rcepts", []).append(key)
                        ratios(p)
            except Exception as e:
                errors.append(f"재무 보완 {k}: {str(e)[:60]}")
        # 연결이 기본인 회사의 별도 시리즈는 fin_s 로 따로 둔다(화면 전환 버튼용)
        for v in co.values():
            f = v.get("fin")
            if f and f.get("alt"):
                v["fin_s"] = f.pop("alt")
            else:
                v.pop("fin_s", None)
        # 주요 재무지표용 추가 항목(총차입금 · 현금성자산 · 감가상각비 · 이자비용) — 원문에서, 접수번호 · 기준별로 저장
        import concurrent.futures as cfu
        import fin_ext
        tasks = []
        for k, v in co.items():
            try:
                tasks.append((k, v, ext_plan(D, v, today)))
            except Exception as e:
                errors.append(f"재무지표 원문 목록 {k}: {str(e)[:50]}")
        need = {(no, cons) for _, _, plan in tasks for (_f, _p, no, cons, _i) in plan if f"x{int(cons)}:{no}" not in afs_cache
                or afs_cache[f"x{int(cons)}:{no}"].get("v") != fin_ext.VERSION}

        def _ext(job):
            no, cons = job
            try:
                return job, fin_ext.extract(no, cons, log)
            except Exception as e:
                log(f"재무지표 원문 읽기 실패 {no}: {e}")
                return job, None
        with cfu.ThreadPoolExecutor(4) as ex:
            for (no, cons), x in ex.map(_ext, sorted(need)):
                if x:
                    afs_cache[f"x{int(cons)}:{no}"] = x
        for k, v, plan in tasks:
            for f, p, no, cons, i in plan:
                x = afs_cache.get(f"x{int(cons)}:{no}")
                vals = (x or {}).get("periods") or []
                e = vals[i] if i < len(vals) else {}
                for kk, src in (("debt", "debt"), ("cash", "cash"), ("da", "da"), ("fc", "int")):  # 금융비용 = 이자비용
                    p.pop(kk, None)
                    if e.get(src) is not None:
                        p[kk] = e[src]
                ratios(p)
                f.setdefault("xr", [])
                if f"x{int(cons)}:{no}" not in f["xr"]:
                    f["xr"].append(f"x{int(cons)}:{no}")
        # 회사 개요(기업개황)와 주주 현황 — 재무와 같은 주기로 갱신
        for k, v in co.items():
            try:
                info = D.json("company.json", corp_code=v["corp_code"])
                if info.get("status") == "000":
                    v["info"] = {x: info.get(y) for x, y in (("nm", "corp_name"), ("ceo", "ceo_nm"), ("est", "est_dt"), ("adres", "adres"),
                                                             ("ind", "induty_code"), ("hm", "hm_url"), ("cls", "corp_cls"))}
            except Exception as e:
                errors.append(f"기업개황 {k}: {str(e)[:40]}")
            try:
                f = v.get("fin") or {}
                sh = api_holders(D, v["corp_code"], today) if not f.get("src") or f.get("src") == "사업보고서 원문" else None
                if not sh:
                    names = [ids.get(k, {}).get("dart_name"), ids.get(k, {}).get("name"), (v.get("info") or {}).get("nm")]
                    sh = doc_holders(D, v["corp_code"], names, today, afs_cache, log)
                if sh:
                    v["sh"] = finish_holders(sh)
            except Exception as e:
                errors.append(f"주주 {k}: {str(e)[:60]}")
            time.sleep(0.1)
    keep = {no for v in co.values() for f in (v.get("fin"), v.get("fin_s")) if f for no in f.get("rcepts", []) + f.get("xr", [])}
    keep |= {"sh4:" + v["sh"]["no"] for v in co.values() if v.get("sh") and v["sh"].get("no")}
    afs_cache = {r: x for r, x in afs_cache.items() if r in keep}
    log(f"DART 호출 {D.calls}회 · 매핑 {len(ids)}개사 · 공시 {sum(len(v['disc']) for v in co.values())}건 · 재무 {sum(1 for v in co.values() if v.get('fin'))}개사")
    return {"generated": now.strftime("%Y-%m-%d %H:%M"), "fin_checked": fin_checked, "co": co, "errors": errors, "afs": afs_cache}


def backlog_collect(res, rows, prev_bl, now, log=print):
    """수주 현황 · 보증·약정 현황 (data/backlog.json) — 정기보고서 · 감사보고서가 바뀐 회사만 다시 읽는다"""
    import backlog
    key = os.environ.get("DART_API_KEY", "").strip()
    D = Dart(key, log) if key else None
    ids = json.loads(IDS_PATH.read_text("utf-8")) if IDS_PATH.exists() else {}
    nm = {r.get("reg") or r["name"]: r["name"] for r in rows}

    def names_of(k):
        m = ids.get(k, {})
        return [x for x in (nm.get(k), m.get("dart_name"), m.get("name"), ((res["co"].get(k) or {}).get("info") or {}).get("nm")) if x]

    co, errors = backlog.collect(D, res.get("co", {}), names_of, (prev_bl or {}).get("co", {}), now.date(), log)
    log(f"수주·보증 {len(co)}개사 (수주 {sum(1 for v in co.values() if v.get('ord'))} · 보증 {sum(1 for v in co.values() if v.get('cg'))})")
    return {"generated": now.strftime("%Y-%m-%d %H:%M"), "co": co, "errors": errors}
