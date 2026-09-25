#!/usr/bin/env python3
"""대상일이 거래일인가. 워크플로 관문에서 쓴다.

    python trading_day.py 2026-09-25   # 쉬는 날이면 종료코드 1

테마 파이프라인은 스캔 -> 그룹 -> 시트 -> 카드 -> 스레드가 한 줄로 이어져
있다. 중간 한 곳만 막으면 뒤가 그대로 나가므로 관문을 맨 앞에 둔다.

## 왜 이벤트 종류로 가르지 않는가

Apps Script 타이머가 workflow_dispatch 로 부른다. 그래서 '수동 실행'과
'예약 실행'을 github.event_name 으로 가를 수 없다 -- 지금 파이프라인의
정규 시계가 바로 그 workflow_dispatch 다. 사람이 부른 실행은 as_of 를
주는 것으로 가린다.
"""
from __future__ import annotations

import sys
from datetime import date

import krx_holidays


def main() -> int:
    if len(sys.argv) < 2:
        print("쓰기: trading_day.py YYYY-MM-DD", file=sys.stderr)
        return 2
    try:
        day = date.fromisoformat(sys.argv[1].strip())
    except ValueError:
        # 날짜를 못 읽으면 막지 않는다. 관문이 조용히 그날 실행을 삼키는
        # 것보다 한 번 더 도는 쪽이 낫다.
        print(f"[경고] 날짜를 읽지 못했습니다: {sys.argv[1]!r} — 막지 않습니다")
        return 0

    if day.weekday() >= 5:
        print(f"{day} 는 주말입니다.")
        return 1
    holiday = krx_holidays.holiday_name(day)
    if holiday:
        print(f"{day} 는 휴장일입니다 ({holiday}).")
        return 1
    if not krx_holidays.known_year(day.year):
        print(f"[경고] {day.year}년 휴장일 달력이 없습니다 — "
              f"krx_holidays.py 에 덧붙이세요")
    print(f"{day} 는 거래일입니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
