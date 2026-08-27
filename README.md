# naver-sheets-autofill

Daily / Monthly Keyword Calendar 자동 입력을 **이 PC 밖에서** 돌리기 위한 리포.

기존에는 작업 스케줄러 `NaverThemeDaily`(평일 20:10)가 헤드리스 `claude -p`를
띄우고, 그 세션이 크롬으로 구글시트를 열어 클립보드로 붙여넣었다. 그래서
**PC가 켜져 있어야만** 동작했고, 절전 중이면 작업이 PC를 깨웠다. 사용자가
"컴퓨터는 절전으로 두고 싶다"고 해서 GitHub Actions + Sheets API로 옮긴다.

## 지금 상태

**아직 실행되지 않는다.** 코드는 끝났고 로직 검증도 끝났지만, 구글 인증이
없어서 시트에 쓸 수 없다. 아래 "설정" 단계를 마쳐야 살아난다.
그때까지는 **로컬 `NaverThemeDaily`(20:10)를 끄지 말 것** — 그게 유일한 경로다.

| | 상태 |
|---|---|
| 테마 스캔 (`theme_scan.py`) | 동작 확인 (로컬 8.4초, 17개 테마 → 8개 그룹) |
| 지수 요약 (`market_summary.py`) | 기존 스크립트 그대로 |
| 네트워크 (GitHub 러너 → 네이버) | 실측 확인 — `finance.naver.com` 4개 경로 전부 200 |
| 행/열 계산 (`theme_write.py`) | `test_theme_write.py` 9건 통과 |
| 구글 시트 쓰기 | **미검증 — 인증 필요** |

## 설정 (사용자가 한 번만 하면 되는 부분)

1. <https://console.cloud.google.com/> 에서 프로젝트를 하나 만든다 (이름 아무거나).
2. **Google Sheets API**를 사용 설정한다:
   <https://console.cloud.google.com/apis/library/sheets.googleapis.com>
3. **IAM 및 관리자 → 서비스 계정 → 서비스 계정 만들기.** 역할은 주지 않아도 된다
   (권한은 시트 공유로만 준다). 만든 뒤 이메일 주소를 복사해 둔다 —
   `이름@프로젝트.iam.gserviceaccount.com` 형태다.
4. 그 서비스 계정의 **키 → 키 추가 → 새 키 만들기 → JSON**. 파일이 하나 받아진다.
5. 아래 두 시트를 **3번의 이메일 주소에 '편집자'로 공유**한다:
   - Daily Keyword Calendar `1xq_WNQh04xV1UsLm9ZRL6HhkQIJn-34G41zm87oR2h4`
   - Monthly Keyword Calendar `1OtZb8hDwb_vK1yGo6StT09jEUytXD9uoxB6Vxmq69ss`
6. 4번에서 받은 JSON 파일 **내용 전체**를 이 리포의 시크릿
   `GOOGLE_SERVICE_ACCOUNT_JSON` 에 넣는다:
   ```
   gh secret set GOOGLE_SERVICE_ACCOUNT_JSON < 받은키.json
   ```
7. `gh workflow run theme-calendar.yml` 로 수동 실행해서 확인한다.

**주의:** 서비스 계정은 사용자 본인 계정이 아니다. 5번의 공유를 하지 않으면
시트를 아예 못 본다 (권한 없음 오류). 반대로 공유한 시트에만 접근하므로
다른 문서가 노출되지는 않는다.

## 구조

```
theme_scan.py       finance.naver.com 테마 스캔 + 병합 -> final_groups.json
market_summary.py   코스피/코스닥 지수·거래대금·외인 -> stdout(JSON)
theme_write.py      두 달력 시트에 기록 (여기만 인증이 필요하다)
sheets.py           Sheets API 얇은 래퍼
keywords.json       키워드 그룹 정의 — 사용자가 직접 고치는 파일
test_theme_write.py 가짜 시트로 행/열 계산 검증 (인증 불필요)
```

`keywords.json` 은 예전에 `C:\Users\USER\Desktop\클로드\키워드.json` 에 있던
파일이다. 러너는 그 경로를 볼 수 없으므로 리포 안으로 옮겼다. **주차별 키워드
큐레이션은 이제 이 파일을 고쳐서 한다** (GitHub 웹에서 바로 편집해도 되고,
로컬에서 고쳐 push해도 된다).

## 안전장치

시트를 잘못 건드리느니 아무것도 안 하는 쪽을 택한다. 다음이면 한 칸도 쓰지 않고 중단한다.

- 오늘 날짜 블록을 A열에서 못 찾음 (그 주 블록이 아직 안 만들어짐)
- 같은 날짜가 두 곳 이상 (어느 블록인지 확정 불가)
- 헤더행에 `종목명` 이 없음 (블록 구조가 예상과 다름)
- 대표행에 `AVERAGEA`/`SUM` 수식이 없음 (템플릿 미준비)
- 스캔 결과 그룹이 0개
- 주말

이미 오늘 것이 채워져 있으면 덮어쓰지 않고 건너뛴다. 그래서 로컬 20:10 작업과
같은 날 둘 다 돌아도 뒤에 오는 쪽이 앞의 결과를 뭉개지 않는다.

대표행의 상승률/거래대금 칸에는 `AVERAGEA`/`SUM` 수식이 들어 있고, 어떤 쓰기
범위에도 그 두 칸은 포함되지 않는다 (`test_theme_write.py` 가 검증한다).

## 알려진 한계

- **GitHub 예약 실행은 많이 밀린다.** 같은 계정의 `industry-report` 리포에서
  cron `0 8` 로 잡은 5회 실행이 전부 113~141분 늦게 시작했고, 8/27은 통째로
  건너뛰었다. 그래서 여기 cron은 정시를 피해 `13 11`(20:13 KST)로 잡았지만,
  이걸로 충분한지는 아직 증명되지 않았다. 저녁 작업이라 2시간 지연은 무해하고
  하루 통째 누락이 진짜 위험이다.
- 60일간 리포에 활동이 없으면 GitHub가 예약 워크플로를 자동 비활성화한다.
- 월간 시트 탭은 분기별이다. 분기가 바뀌면 새 탭이 필요한데 **탭 생성은
  자동화하지 않았다** — 못 찾으면 중단하고 사람을 기다린다.
- 슬롯(6개)보다 그룹이 많으면 뒤쪽을 버린다. 슬롯을 늘리는 건 수동 작업이다.
- `supply_demand.py` 는 예약 실행되지 않는다. 수급동향 시트는 별도의 Apps
  Script 가 담당한다 — 파일 안 설명 참조.
