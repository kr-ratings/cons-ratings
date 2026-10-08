# 시공사 시공능력평가 · 신용등급 현황 대시보드

대한건설협회 시공능력평가(도급순위) 상위 100개사와 신용평가 3사(한국신용평가·한국기업평가·나이스신용평가) 회사채 신용등급을 한 화면에 보여 주는 정적 사이트입니다. 디자인은 [유동화시장 등급 · 금리 현황 대시보드](https://daily-ratings.pages.dev/)와 같은 토큰을 씁니다.

사이트: https://cons-ratings.pages.dev (Cloudflare Pages) · https://kr-ratings.github.io/cons-ratings/ (GitHub Pages)

## 구성

| 경로 | 내용 |
|---|---|
| `index.html` | 화면 전체(HTML·CSS·JS 한 파일) |
| `data/contractors.json` | 순위·평가액·등급 데이터 (수집기가 씀) |
| `data/status.json` | 마지막 수집 시각과 출처별 성공 여부 |
| `collector/collect.py` | 원 출처 수집기 |
| `collector/agency_ids.json` | 회사별 평가사 기업코드 매핑 (등록번호 기준) |
| `.github/workflows/update.yml` | 매주 월요일 07:10 KST 자동 실행 |

## 갱신 방식

1. **시공능력평가**: 대한건설협회 공시자료 게시판에서 가장 최근 연도 `종합건설사업자 시공능력평가액 공시` 글의 엑셀을 받아 `토건` 시트 상위 100개사를 읽습니다. 직전 연도 엑셀로 전년 순위를 붙입니다(건설업 등록번호로 매칭). 매년 7월 말 새 공시나 정정 공시가 올라오면 다음 실행 때 자동 반영됩니다.
2. **신용등급**: `agency_ids.json`의 코드로 각 평가사 기업 페이지를 열어 현재 회사채 신용등급·Outlook·평가일을 읽습니다. 한기평은 유효기간이 끝난 등급을 제외하고, 무보증 등급을 보증 등급보다 우선합니다.
3. 한 출처가 실패하면 직전 값을 유지하고 화면 상단 수집 상태에 빨간 점으로 표시합니다.

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

GitHub에서는 Actions 탭 → `주간 데이터 갱신` → `Run workflow`로 바로 돌릴 수 있습니다.
