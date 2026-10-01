# 세션 메모

## "체크리스트"라고 하면

보유·관심 종목 **매수 체크리스트 대장** 아티팩트를 말한다. 레포 안 파일이 아니다.

- 대장: https://claude.ai/artifact/7u6bVGDBxvqR5VFkCbuCRV (Artifact `read` 로 연다)
- 종목 하나짜리 예: GS리테일 매수 체크리스트 https://claude.ai/artifact/MLHMDozaaEtBWP9ruNTpt2
- 항목 11개: 01 CB 물량 · 02 오버행 · 03 매출 아이템별 하락 · 04 매출·영업이익 하락 ·
  05 오너 리스크 · 06 유상증자 가능성 · 07 성장성 감소 · 08 대주주 지분 변동 ·
  09 현금흐름·재무 안전성 · 10 주요 고객사 집중도 · 11 PER·PBR(판정 없이 수치만)
- 판정은 ○ 양호 / △ 주의(조건부·미공개) / ✕ 위험. 근거는 DART 공시·재무제표와 KRX 시세.
- 갱신할 때는 대장을 먼저 읽고 같은 양식으로 이어서 고친다.

## API 키

- `DART_API_KEY`, `KRX_API_KEY` 환경변수에 들어 있다. 값 뒤에 메모가 공백으로 붙어 있으니
  **첫 토큰만** 쓴다: `os.environ["DART_API_KEY"].split()[0]` (40자리).
- KRX Open API(`data-dbg.krx.co.kr/svc/apis/...`)는 헤더 `AUTH_KEY` 로 보낸다.
  `Unauthorized Key` 는 키가 틀린 것, `Unauthorized API Call` 은 키는 맞고 그 API 이용신청이 안 된 것.

## 네트워크

이 클라우드 환경에서 열린 곳: `opendart.fss.or.kr`, `dart.fss.or.kr`, `data-dbg.krx.co.kr`,
`m.stock.naver.com`, `api.stock.naver.com`. 막힌 곳(403): `data.krx.co.kr`, `stock.naver.com`,
`finance.naver.com`, `finance.daum.net`, `holidayapi.kr`, `date.nager.at`.
