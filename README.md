# 시공사 시공능력평가 · 신용등급 현황 대시보드

대한건설협회 시공능력평가(도급순위) 상위 100개사와 신용평가 3사(한국신용평가·한국기업평가·나이스신용평가) 회사채 신용등급을 한 화면에 보여 주는 정적 사이트입니다. 디자인은 [유동화시장 등급 · 금리 현황 대시보드](https://abs.namepf.com/)와 같은 토큰을 씁니다.

사이트: https://cons.namepf.com (Cloudflare Pages) · https://kr-ratings.github.io/cons-ratings/ (GitHub Pages)

## 구성

| 경로 | 내용 |
|---|---|
| `index.html` | 화면 전체(HTML·CSS·JS 한 파일) |
| `data/contractors.json` | 순위·평가액·등급 데이터 (수집기가 씀) |
| `data/status.json` | 마지막 수집 시각과 출처별 성공 여부 |
| `collector/collect.py` | 원 출처 수집기 |
| `collector/agency_ids.json` | 회사별 평가사 기업코드 매핑 (등록번호 기준) |
| `collector/news.py` · `news_names.json` | 회사별 기사 검색(구글 뉴스 RSS, 제목·출처·링크만)과 검색용 이름 |
| `collector/dart.py` · `dart_ids.json` | DART 주요 공시·재무 요약(환경변수 `DART_API_KEY`)과 회사별 DART 고유번호 |
| `collector/cost.py` | 건설공사비지수(한국건설기술연구원) |
| `collector/events.py` | 직전 수집과 비교한 등급·Outlook·시평 변동 기록 |
| `data/history.json` · `events.json` · `news.json` · `dart.json` · `cost.json` | 등급 이력 · 변동 · 기사 · 공시/재무 · 공사비 |
| `.github/workflows/update.yml` | 매일 오전(08:00) 자동 실행 (07:50 KST 시작) |

## 갱신 방식

1. **시공능력평가**: 대한건설협회 공시자료 게시판에서 가장 최근 연도 `종합건설사업자 시공능력평가액 공시` 글의 엑셀을 받아 `토건` 시트 상위 100개사를 읽습니다. 직전 연도 엑셀로 전년 순위를 붙입니다(건설업 등록번호로 매칭). 매년 7월 말 새 공시나 정정 공시가 올라오면 다음 실행 때 자동 반영됩니다.
2. **신용등급**: `agency_ids.json`의 코드로 각 평가사 기업 페이지를 열어 현재 회사채 신용등급·Outlook·평가일을 읽습니다. 한기평은 유효기간이 끝난 등급을 제외하고, 무보증 등급을 보증 등급보다 우선합니다.
3. **등급 이력**: NICE·KR은 기업 페이지의 회차별 등급 이력 표를, KIS는 매일 본 현재 등급을 쌓아 만듭니다.
4. **기사**: 회사마다 최근 3일 기사를 검색해 제목에 회사 이름이 있고 리스크·신용·실적·수주·지배구조 주제인 것만 30일치 남깁니다. 검색 이름이 정식 사명과 다른 회사(GS건설, DL이앤씨 등)는 `news_names.json`에 적습니다.
5. **DART**: 저장소 Settings → Secrets and variables → Actions 에 `DART_API_KEY`(Open DART 인증키)가 있으면 최근 90일 주요 공시와 재무 요약(최근 5개 사업연도 + 올해 최신 분기)을 받습니다. 없으면 이 부분만 건너뜁니다.
6. **공사비지수**: cost.kict.re.kr 게시판의 최신 월 엑셀(2000년~ 전 기간)을 받습니다.
7. 한 출처가 실패하면 직전 값을 유지하고 화면 상단 수집 상태에 빨간 점으로 표시합니다.

## 평가사 코드 추가 (새로 100위 안에 든 회사)

화면에 `평가사 코드 미등록 n개사`가 뜨면 `collector/agency_ids.json`에 한 줄을 추가합니다. 코드는 각 평가사 기업 페이지 주소에 있습니다.

```json
"회사명(법인격 표기·공백 제거)": {"name": "표시용 회사명", "reg": "등록번호", "KIS": "kiscd", "KR": "COMP_CD", "NICE": "cmpCd"}
```

- KIS: `kisrating.com/ratingsSearch/corp_overview.do?kiscd=…`
- KR: `korearatings.com/cms/frDisclosureCon/compView.do?…&COMP_CD=…`
- NICE: `nicerating.com/disclosure/companyGradeInfo.do?cmpCd=…`

평가사에 등록이 없는 회사는 코드를 비워 두면 됩니다(등급 칸이 – 로 표시).

## 수동 실행

```bash
pip install requests beautifulsoup4 openpyxl
python collector/collect.py
```

GitHub에서는 Actions 탭 → `수집 및 배포` → `Run workflow`로 바로 돌릴 수 있습니다.
