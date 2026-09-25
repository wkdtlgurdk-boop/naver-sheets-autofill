"""한국거래소 휴장일. 아침 06:40 실행은 장이 열리기 전이라 데이터로는 오늘이
거래일인지 알 수 없다 -- 지수도 일봉도 아직 전 거래일 값이다. 그래서 달력을 둔다.

2026-09-24(추석 연휴)에 9/23 종가 기준 추천이 휴장일 아침에 나갔다. 다음 거래일
아침에 같은 기준일로 다시 나갈 것이므로, 휴장일 아침 발송은 정보가 없는 알림이다.

**해마다 거래소 공고를 보고 다음 해를 덧붙인다.** 목록에 없는 해는 막지 않는다
(주말만 막는다) -- 모르는 날을 휴장으로 치면 평일 아침 알림이 조용히 사라진다.

이 파일은 네 리포에 같은 내용으로 들어 있다. 원본은 volume-surge-telegram-bot
의 tradesignal/krx_holidays.py 이고 나머지는 복사본이다. 리포가 서로를 못
불러오니 어쩔 수 없다 -- **고칠 때는 넷을 같이 고친다.**

    volume-surge-telegram-bot/tradesignal/   tradesignal · holdings-brief
                                             insider-buy · prognosis
    naver-sheets-autofill/                   theme-calendar · supply-demand
    industry-report-telegram-bot/            industry-report
    chart-tracking/                          chart-tracking
"""
from __future__ import annotations

from datetime import date

# 2026년 17일. 제헌절(7/17)이 공휴일로 돌아오고 지방선거(6/3)가 들었다.
# 추석 마지막 날(9/26)이 토요일이지만 추석 대체공휴일은 일요일·공휴일과 겹칠
# 때만 생겨 9/28 은 거래일로 본다. 거래소가 달리 공고하면 여기에 넣는다.
HOLIDAYS = {
    2026: {
        "0101": "신정",
        "0216": "설날", "0217": "설날", "0218": "설날",
        "0302": "삼일절 대체",
        "0501": "근로자의 날",
        "0505": "어린이날",
        "0525": "부처님오신날 대체",
        "0603": "지방선거",
        "0717": "제헌절",
        "0817": "광복절 대체",
        "0924": "추석", "0925": "추석",
        "1005": "개천절 대체",
        "1009": "한글날",
        "1225": "성탄절",
        "1231": "연말 휴장",
    },
}


def holiday_name(day: date) -> str | None:
    """휴장일이면 이름, 아니면 None. 주말은 여기서 보지 않는다."""
    return HOLIDAYS.get(day.year, {}).get(f"{day:%m%d}")


def known_year(year: int) -> bool:
    return year in HOLIDAYS
