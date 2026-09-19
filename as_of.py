"""이 실행이 '어느 날'을 대상으로 하는지 한 곳에서 정한다.

같은 답이 필요한 곳이 다섯이다 — 시트 블록을 찾는 theme_write, 카드에 찍히는
날짜와 폴더 이름을 만드는 cards, 스레드에 올릴 이미지 URL 을 만드는
post_threads, 시트를 내보내는 feed_export, 그리고 카드를 공개 리포에 올리는
워크플로 단계. 각자 datetime.now() 를 부르던 탓에 2026-09-11 을 다시 채우는
실행이 시트는 9/11 에 쓰고 카드는 9/13(일) 로 찍어 스레드에까지 올렸다.

## 두 번째 사고: 자정을 넘긴 실행 (2026-09-18)

같은 병이 다른 모양으로 또 났다. 그날 예약 실행이 3시간 34분 늦어 23:54 에
시작하고 00:01 에 끝났다. 앞 단계(스캔·시트)는 금요일 23:5x 에 돌아 9/18 을
봤고, 뒤 단계(카드·스레드)는 토요일 00:0x 에 돌아 9/19 를 봤다. 시트에는
9/18 이 적히고 카드와 스레드에는 9/19 가 찍혔다.

원인이 둘이었다.

1. 거래일 규칙이 틀렸다. `now.hour < 20 and now.weekday() < 5` 였는데, 토요일
   00:01 은 `hour < 20` 이지만 `weekday() == 5` 라 어제로 물러나지 않았다.
   금요일 저녁 실행이 자정을 넘기는 순간 토요일이 대상일이 된다.
2. 규칙이 맞았어도 단계마다 시계를 다시 봤다. 20:00 경계를 걸치는 실행이면
   여전히 갈라진다.

그래서 규칙을 `trading_day` 로 바로잡고(주말은 금요일까지 물러난다), 워크플로가
실행 시작 때 대상일을 한 번 계산해 THEME_DAY 에 박아 두게 했다. 그다음부터는
어느 단계가 몇 시에 돌든 같은 날을 본다.

## 환경변수 둘

    THEME_AS_OF   사람이 지목한 날짜. 지난 날짜를 다시 채울 때 쓴다. 이게
                  있으면 주말 가드를 건너뛰고, 부르는 쪽은 '되살리는 실행'
                  으로 알고 평소와 다르게 군다(스레드에 다시 올리지 않는 등).
    THEME_DAY     워크플로가 박아 둔 대상일. 사람의 지목이 아니라 **이번 실행이
                  이미 정한 답**이므로 되살리기 취급을 하지 않는다.

둘 다 YYYY-MM-DD. 휴장일은 THEME_AS_OF 로 수동 처리한다.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# KRX 정규장 마감. 이 시각 전이면 오늘 종가가 아직 없다.
#
# 15:30 이었다가 거래시간이 늘면서 20:00 이 됐다. 이 상수가 곧 "오늘 것을
# 봐도 되는가"의 기준선이다.
CLOSE_HOUR = 20

AS_OF_ENV = "THEME_AS_OF"
PIN_ENV = "THEME_DAY"


def _parse(raw: str, name: str) -> datetime:
    try:
        day = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise SystemExit(f"[중단] {name} 는 YYYY-MM-DD 형식이어야 합니다: {raw!r}")
    return day.replace(tzinfo=KST)


def trading_day(now: datetime) -> datetime:
    """`now` 시점에서 '종가가 확정된 가장 최근 거래일'. 자정 기준으로 돌려준다.

    마감 전이면 하루 물러나고, 그 자리가 주말이면 금요일까지 더 물러난다.
    두 번째 조건이 중요하다 -- 금요일 저녁 실행이 자정을 넘겨도 토요일 00:01
    에서 금요일로 되돌아오므로, 자정을 걸치는 실행이 앞뒤 단계에서 갈라지지
    않는다. 20:00 경계도 마찬가지로 THEME_DAY 가 막아 준다.
    """
    day = now if now.hour >= CLOSE_HOUR else now - timedelta(days=1)
    while day.weekday() > 4:            # 5=토, 6=일
        day -= timedelta(days=1)
    return day.replace(hour=0, minute=0, second=0, microsecond=0)


def as_of() -> tuple[datetime, bool]:
    """(대상 날짜, 사람이 지목했는지).

    두 번째 값이 True 면 지난 날짜를 손보는 실행이다. 부르는 쪽은 그걸 보고
    평소와 다르게 굴 수 있다 — 주말 가드를 건너뛰거나, 이미 지난 날짜를
    스레드에 다시 올리지 않거나.

    THEME_DAY 는 True 를 만들지 않는다. 워크플로가 방금 계산해 박아 둔 값이라
    평소 실행과 같은 답이기 때문이다. 여기서 True 를 주면 정규 실행이 매번
    '되살리는 실행'으로 오인되어 스레드 게시가 조용히 꺼진다.
    """
    raw = os.environ.get(AS_OF_ENV, "").strip()
    if raw:
        return _parse(raw, AS_OF_ENV), True

    pinned = os.environ.get(PIN_ENV, "").strip()
    if pinned:
        return _parse(pinned, PIN_ENV), False

    return trading_day(datetime.now(KST)), False


if __name__ == "__main__":
    # 워크플로 첫 단계가 이걸 불러 대상일을 한 번 정하고 THEME_DAY 에 박는다.
    print(f"{as_of()[0]:%Y-%m-%d}")
