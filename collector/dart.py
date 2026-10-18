"""DART(금융감독원 전자공시) 공시 · 재무 수집 — 환경변수 DART_API_KEY 필요(Open DART 무료 인증키)

1) 고유번호 매핑: corpCode.xml 의 회사명을 시평 회사명과 맞춘다. 같은 이름이 여럿이면 업종(건설업)으로 고른다.
   결과는 collector/dart_ids.json 에 저장해 두고 다음부터 재사용한다(직접 고쳐 넣어도 된다).
2) 공시: 회사별 최근 공시 목록(list.json)에서 실무에 의미 있는 공시만 분류해 최근 90일치를 남긴다.
3) 재무: 다중회사 주요계정(fnlttMultiAcnt)으로 최근 사업연도 3개년 + 올해 최신 분기(누적)를 받는다.
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
            for c in sorted(cands, key=lambda c: (bool(c["stock_code"]), c["modify_date"]), reverse=True)[:20]:
                try:
                    info = D.json("company.json", corp_code=c["corp_code"])
                except Exception:
                    continue
                ind = str(info.get("induty_code") or "")
                score = (ind[:2] in ("41", "42"), bool(c["stock_code"]), c["modify_date"])
                scored.append((score, c))
                time.sleep(0.1)
            if scored:
                pick = max(scored, key=lambda x: x[0])[1]
        if pick:
            ids[r.get("reg") or r["name"]] = {"name": r["name"], "corp_code": pick["corp_code"], "dart_name": pick["corp_name"],
                                              "stock": pick["stock_code"] or None}
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
            if not c:
                continue
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
            xs = [x for x in xs if x.get("fs_div") == fs]
            cur, prv, pp = {}, {}, {}
            for x in xs:
                k = ACCT.get(x.get("account_nm", "").strip())
                if not k or k in cur:
                    continue
                cur[k] = _amt(x.get("thstrm_amount"))
                prv[k] = _amt(x.get("frmtrm_amount"))
                pp[k] = _amt(x.get("bfefrmtrm_amount"))
            ent = res.setdefault(cc, {"basis": "연결" if fs == "CFS" else "별도", "periods": []})
            if label == "FY":
                for y, vals in ((bsns_year, cur), (bsns_year - 1, prv), (bsns_year - 2, pp)):
                    if any(v is not None for v in vals.values()):
                        ent["periods"].append({"p": f"{y}", "t": "FY", **vals})
                ent["fy_rcept"] = xs[0].get("rcept_no") if xs else None
            else:
                if any(v is not None for v in cur.values()):
                    ent["periods"].insert(0, {"p": f"{bsns_year} {label}", "t": label, **cur})
                    ent["q_rcept"] = xs[0].get("rcept_no") if xs else None

    chunks = [codes[i:i + 90] for i in range(0, len(codes), 90)]
    # 최근 사업연도: 올해 3월 이후면 작년, 아니면 재작년부터 시도
    for ch in chunks:
        pending = set(ch)
        for y in (year - 1, year - 2):
            if not pending:
                break
            d = D.json("fnlttMultiAcnt.json", corp_code=",".join(sorted(pending)), bsns_year=y, reprt_code="11011")
            if d.get("status") == "000":
                lst = d.get("list", [])
                take(lst, y, "FY")
                pending -= {x["corp_code"] for x in lst}
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
        for p in ent["periods"]:
            if p.get("liab") is not None and p.get("equity"):
                p["dr"] = round(p["liab"] / p["equity"] * 100, 1) if p["equity"] > 0 else None
            if p.get("op") is not None and p.get("rev"):
                p["opm"] = round(p["op"] / p["rev"] * 100, 1)
    return res


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
        old = [x for x in p.get("disc", []) if x["d"] >= since_full]
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
    if not last or new_periodic or (today - dt.date.fromisoformat(last)).days >= FIN_REFRESH_DAYS:
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
    log(f"DART 호출 {D.calls}회 · 매핑 {len(ids)}개사 · 공시 {sum(len(v['disc']) for v in co.values())}건 · 재무 {sum(1 for v in co.values() if v.get('fin'))}개사")
    return {"generated": now.strftime("%Y-%m-%d %H:%M"), "fin_checked": fin_checked, "co": co, "errors": errors}
