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
    ("리스크", r"부도|영업정지|회생|파산|해산|소송|감자|관리절차|횡령|배임|불성실|채권은행|상장폐지|감사의견|거래정지"),
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
    """{corp_code: {"basis": "연결"/"별도", "periods": [...]}} — 다중회사 주요계정, 100개씩"""
    year = today.year
    res = {}

    def take(lst, bsns_year, label):
        by = {}
        for x in lst:
            by.setdefault(x["corp_code"], []).append(x)
        for cc, xs in by.items():
            fs = "CFS" if any(x.get("fs_div") == "CFS" for x in xs) else "OFS"
            if cc in res:  # 이미 받은 기간과 같은 기준(연결/별도)으로 맞춘다
                want = "CFS" if res[cc]["basis"] == "연결" else "OFS"
                if not any(x.get("fs_div") == want for x in xs):
                    continue
                fs = want
            xs = [x for x in xs if x.get("fs_div") == fs]
            cur, prv, pp = {}, {}, {}
            for x in xs:
                k = acct_key(x.get("account_nm", ""))
                if not k or k in cur:
                    continue
                # 분기·반기 손익은 thstrm_amount 가 3개월치라서 누적(thstrm_add_amount)을 쓴다
                cur[k] = _amt(x.get("thstrm_add_amount")) if (label != "FY" and k in ("rev", "op", "ni") and x.get("thstrm_add_amount")) else _amt(x.get("thstrm_amount"))
                prv[k] = _amt(x.get("frmtrm_amount"))
                pp[k] = _amt(x.get("bfefrmtrm_amount"))
            ent = res.setdefault(cc, {"basis": "연결" if fs == "CFS" else "별도", "periods": []})
            if label == "FY":
                have = {p["p"]: p for p in ent["periods"]}
                for y, vals in ((bsns_year, cur), (bsns_year - 1, prv), (bsns_year - 2, pp)):
                    if f"{y}" in have:  # 이미 있는 연도는 빈 항목만 다른 보고서 값으로 채운다
                        for k, v in vals.items():
                            if have[f"{y}"].get(k) is None and v is not None:
                                have[f"{y}"][k] = v
                    elif any(v is not None for v in vals.values()):
                        ent["periods"].append({"p": f"{y}", "t": "FY", **vals})
                ent.setdefault("fy_rcept", xs[0].get("rcept_no") if xs else None)
            else:
                if any(v is not None for v in cur.values()):
                    ent["periods"].insert(0, {"p": f"{bsns_year} {label}", "t": label, **cur})
                    ent["q_rcept"] = xs[0].get("rcept_no") if xs else None

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
        nfy = lambda c: sum(1 for p in res.get(c, {}).get("periods", []) if p["t"] == "FY"
                            and all(p.get(k) is not None for k in ("rev", "op", "ni", "assets", "liab", "equity")))
        for off in (2, 1, 3):
            for y in sorted(set(latest.values())):
                cs = sorted(c for c, v in latest.items() if v == y and nfy(c) < 5)
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
    for ent in res.values():
        for p in ent["periods"]:  # 자산 = 부채 + 자본 으로 빠진 한 항목 보완
            a, l, e = p.get("assets"), p.get("liab"), p.get("equity")
            if e is None and a is not None and l is not None:
                p["equity"] = a - l
            elif l is None and a is not None and e is not None:
                p["liab"] = a - e
        fy = sorted((p for p in ent["periods"] if p["t"] == "FY"), key=lambda p: p["p"], reverse=True)[:5]
        ent["periods"] = [p for p in ent["periods"] if p["t"] != "FY"] + fy
        for p in ent["periods"]:
            if p.get("liab") is not None and p.get("equity"):
                p["dr"] = round(p["liab"] / p["equity"] * 100, 1) if p["equity"] > 0 else None
            if p.get("op") is not None and p.get("rev"):
                p["opm"] = round(p["op"] / p["rev"] * 100, 1)
    return res


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


def audit_fin(D, corp_code, today, cache, log=print):
    """구조화 재무가 없는 회사: 감사보고서(없으면 사업보고서) 원문에서 최근 5개 사업연도 → (fin, 최신 감사보고서)"""
    import audit_fs
    since = (today - dt.timedelta(days=365 * 7)).strftime("%Y%m%d")
    reps = _reports(D, corp_code, since, "F", AUD_RX, "audit")
    reps.update(_reports(D, corp_code, since, "A", BIZ_RX, "biz"))
    if not reps:
        return None, None
    latest_y = max(r["y"] for r in reps.values())
    cons = ("audit", True, latest_y) in reps or not any(r["kind"] == "audit" and r["y"] == latest_y for r in reps.values())
    auds = [r for r in reps.values() if r["kind"] == "audit"]
    latest = max(auds, key=lambda r: (r["y"], r["c"] == cons, r["no"])) if auds else None
    want = [latest_y - i for i in range(5)]
    # 같은 기준(연결/별도) 감사보고서 → 사업보고서 → 다른 기준 감사보고서, 각각 최신 연도부터
    order = sorted([r for r in reps.values() if r["kind"] == "audit" and r["c"] == cons], key=lambda r: -r["y"]) + \
            sorted([r for r in reps.values() if r["kind"] == "biz"], key=lambda r: -r["y"]) + \
            sorted([r for r in reps.values() if r["kind"] == "audit" and r["c"] != cons], key=lambda r: -r["y"])
    periods, partial, rcepts = {}, {}, []
    full = lambda v: v.get("assets") is not None and (v.get("rev") is not None or v.get("op") is not None)
    for r in order:
        if all(y in periods for y in want):
            break
        if not any(r["y"] - i in want and r["y"] - i not in periods for i in range(3)):
            continue
        x = cache.get(r["no"])
        if not x or x.get("v") != audit_fs.VERSION:
            try:
                x = cache[r["no"]] = audit_fs.parse(r["no"], prefer_cons=cons) or {"v": audit_fs.VERSION}
            except Exception as e:
                log(f"재무제표 읽기 실패 {r['no']}: {e}")
                continue
            time.sleep(0.5)
        used = False
        for i, vals in enumerate(x.get("periods") or []):
            y = r["y"] - i
            if y not in want or y in periods:
                continue
            ent = {"p": f"{y}", "t": "FY", **{k: vals.get(k) for k in ("rev", "op", "ni", "assets", "liab", "equity")}}
            if x.get("consolidated") != cons:
                ent["b"] = "연결" if x.get("consolidated") else "별도"
            if full(vals):
                periods[y] = ent; used = True
            elif vals.get("assets") is not None and y not in partial:
                partial[y] = ent; used = True
        if used:
            rcepts.append(r["no"])
    for y, ent in partial.items():  # 끝까지 손익을 못 찾은 연도는 있는 숫자라도
        periods.setdefault(y, ent)
    if not periods:
        return None, latest
    ps = [periods[y] for y in sorted(periods, reverse=True)]
    for p in ps:
        if p.get("liab") is not None and p.get("equity"):
            p["dr"] = round(p["liab"] / p["equity"] * 100, 1) if p["equity"] > 0 else None
        if p.get("op") is not None and p.get("rev"):
            p["opm"] = round(p["op"] / p["rev"] * 100, 1)
    src = latest or max(reps.values(), key=lambda r: r["y"])
    return {"basis": "연결" if cons else "별도", "src": "감사보고서" if latest else "사업보고서 원문", "periods": ps, "rcepts": rcepts,
            "audit_no": src["no"], "audit_nm": src["nm"]}, latest


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
                 **({"fin": p["fin"]} if p.get("fin") else {}), **({"audit": p["audit"]} if p.get("audit") else {})}
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
        # 재무 API에 빠진 항목(예: 일부 연도 당기순이익)은 그해 사업보고서 원문 재무제표에서 채운다
        F6 = ("rev", "op", "ni", "assets", "liab", "equity")
        for k, v in co.items():
            f = v.get("fin")
            if not f or f.get("src"):
                continue
            gaps = [p for p in f["periods"] if p["t"] == "FY" and any(p.get(x) is None for x in F6)]
            if not gaps:
                continue
            try:
                biz = _reports(D, v["corp_code"], (today - dt.timedelta(days=365 * 7)).strftime("%Y%m%d"), "A", BIZ_RX, "biz")
                for p in gaps:
                    r = biz.get(("biz", None, int(p["p"])))
                    if not r:
                        continue
                    x = afs_cache.get(r["no"])
                    if not x or x.get("v") != audit_fs.VERSION:
                        x = afs_cache[r["no"]] = audit_fs.parse(r["no"], prefer_cons=f["basis"] == "연결") or {"v": audit_fs.VERSION}
                        time.sleep(0.5)
                    # 연결 기준인데 원문에 별도만 있으면 그해는 종속회사가 없어 연결 = 별도인 경우라 그대로 쓴다
                    if not x.get("periods") or (f["basis"] == "별도" and x.get("consolidated")):
                        continue
                    for key in F6:
                        if p.get(key) is None and x["periods"][0].get(key) is not None:
                            p[key] = x["periods"][0][key]
                    f.setdefault("rcepts", []).append(r["no"])
                    if p.get("liab") is not None and p.get("equity"):
                        p["dr"] = round(p["liab"] / p["equity"] * 100, 1) if p["equity"] > 0 else None
                    if p.get("op") is not None and p.get("rev"):
                        p["opm"] = round(p["op"] / p["rev"] * 100, 1)
            except Exception as e:
                errors.append(f"재무 보완 {k}: {str(e)[:60]}")
    keep = {no for v in co.values() if v.get("fin") for no in v["fin"].get("rcepts", [])}
    afs_cache = {r: x for r, x in afs_cache.items() if r in keep}
    log(f"DART 호출 {D.calls}회 · 매핑 {len(ids)}개사 · 공시 {sum(len(v['disc']) for v in co.values())}건 · 재무 {sum(1 for v in co.values() if v.get('fin'))}개사")
    return {"generated": now.strftime("%Y-%m-%d %H:%M"), "fin_checked": fin_checked, "co": co, "errors": errors, "afs": afs_cache}
