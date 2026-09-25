#!/usr/bin/env python3
"""krx_holidays.py 에 붙일 다음 해 달력을 만든다. 해가 바뀌기 전에 한 번.

    python make_holidays.py 2027

찍힌 블록을 krx_holidays.py 의 HOLIDAYS 에 붙여 넣는다. 그 파일은 네 리포에
같은 내용으로 들어 있으므로 **넷을 같이 고친다.**

    volume-surge-telegram-bot/tradesignal/krx_holidays.py   (원본)
    naver-sheets-autofill/krx_holidays.py
    industry-report-telegram-bot/krx_holidays.py
    chart-tracking/krx_holidays.py

## 왜 API 를 봇이 직접 안 부르는가

공휴일 목록과 KRX 휴장일이 같지 않다. 손질이 필요한데, 그 손질을 봇 아홉이
매 실행마다 되풀이할 이유가 없다. 한 번 만들어 파일로 두고 다 같이 읽는다.
외부 API 가 죽어도 그날 알림은 그대로 나간다 -- 네트워크를 타지 않는 것이
이 방식의 값어치다.

**찍힌 것을 그대로 믿지 말 것.** nager 는 2027-05-03 을 근로자의 날로 주는데
그 해 5/1 은 토요일이고 근로자의 날은 대체공휴일 대상이 아니다. 거래소 공고와
맞춰 보고 붙인다. 틀린 날을 넣으면 그날 알림이 조용히 사라진다.

## 두 출처를 맞춰 본다

    date.nager.at      공휴일 목록. 앞날을 안다. 열쇠가 필요 없다.
                       (holidayapi.kr 은 2026-09-25 기준 DNS 부터 죽었다)
    네이버 지수 price  실제로 장이 선 날짜. 지난 날짜에 대해서는 이쪽이
                       옳다 -- 임시공휴일이든 무엇이든 결과가 찍혀 있다.

2026-09-25 실측에서 둘이 갈린 곳은 딱 둘이었다.

    12/31 연말휴장   nager 에 없다. 2024·2025 둘 다 장이 안 섰다. 넣는다.
    임시공휴일       nager 에 없다. 2025-01-27 이 그랬다. 예측할 수 없으므로
                     생기면 사람이 --extra 로 넣는다.

반대로 **제헌절(7/17)은 넣어야 한다.** 2008년부터 공휴일이 아니었지만
2026년부터 되살아났다 -- 2025-07-17 은 장이 섰고 2026-07-17 은 안 섰다.
기억으로 빼지 말 것. 이 대조가 그것을 잡아냈다.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

NAGER = "https://date.nager.at/api/v3/PublicHolidays/{year}/KR"
PRICE = "https://m.stock.naver.com/api/index/KOSPI/price?pageSize=60&page={page}"
NAVER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://m.stock.naver.com/",
    "User-Agent": "Mozilla/5.0 make-holidays/1.0",
}


def get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "make-holidays/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def public_holidays(year: int) -> list[str]:
    """그 해 공휴일(YYYY-MM-DD). 주말도 그대로 둔다 -- 거르는 쪽이 판단한다."""
    rows = get(NAGER.format(year=year))
    return sorted({str(r.get("date") or "")[:10] for r in rows if r.get("date")})


def year_end_close(year: int) -> list[str]:
    """연말휴장일. 12/31 이 평일일 때만 휴장이다.

    주말이면 그 주 마지막 거래일이 이미 그 앞이라 따로 더할 것이 없다.
    """
    last = date(year, 12, 31)
    return [last.isoformat()] if last.weekday() < 5 else []


def traded_days() -> set[str]:
    """네이버가 아는 실제 거래일. 대조용이라 실패해도 죽지 않는다."""
    out: set[str] = set()
    for page in range(1, 9):
        try:
            rows = get(PRICE.format(page=page), NAVER_HEADERS)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            break
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            day = str((row or {}).get("localTradedAt") or "")
            if len(day) == 10:
                out.add(day)
    return out


def cross_check(year: int, holidays: set[str], traded: set[str]) -> list[str]:
    """실측과 어긋나는 날을 말로 적는다. 고치지는 않는다 -- 사람이 본다."""
    if not traded:
        return ["  (거래일 자료를 못 받아 대조하지 못했다)"]
    first, last = min(traded), max(traded)
    notes = []
    for day in sorted(holidays):
        if day < first or day > last:
            continue                      # 자료 바깥은 판단하지 않는다
        if date.fromisoformat(day).weekday() >= 5:
            continue                      # 주말은 어차피 장이 없다
        if day in traded:
            notes.append(f"  [어긋남] {day} 를 휴장으로 넣었는데 장이 섰다")

    cur, end = date.fromisoformat(first), date.fromisoformat(last)
    while cur <= end:
        iso = cur.isoformat()
        if (cur.year == year and cur.weekday() < 5
                and iso not in traded and iso not in holidays):
            notes.append(f"  [빠짐] {iso} 은 장이 안 섰는데 달력에 없다 "
                         f"(임시공휴일?)")
        cur += timedelta(days=1)
    return notes or ["  실측과 어긋나는 날 없음"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("year", type=int, nargs="?", default=date.today().year)
    ap.add_argument("--extra", nargs="*", default=[],
                    help="손으로 더할 날 (임시공휴일 등). YYYY-MM-DD")
    args = ap.parse_args()

    days = set(public_holidays(args.year))
    print(f"[공휴일] date.nager.at 에서 {len(days)}건")

    added = set(year_end_close(args.year)) - days
    if added:
        print(f"[연말휴장] {sorted(added)} 더함 (nager 에 없다)")
    days |= set(year_end_close(args.year))

    extra = {d for d in args.extra if d}
    if extra:
        print(f"[손으로] {sorted(extra)} 더함")
    days |= extra

    print(f"\n[대조] 네이버 실제 거래일과 맞춰 본다")
    for line in cross_check(args.year, days, traded_days()):
        print(line)

    weekdays = [d for d in sorted(days)
                if date.fromisoformat(d).weekday() < 5]
    print(f"\n[블록] krx_holidays.py 의 HOLIDAYS 에 붙여 넣는다 "
          f"({len(weekdays)}일, 주말은 뺐다 -- 주말은 달력 없이도 막힌다)")
    print(f"    {args.year}: {{")
    for day in weekdays:
        print(f'        "{day[5:7]}{day[8:10]}": "",')
    print("    },")
    print("\n이름은 비워 두었다. 알림 본문에 그대로 찍히므로 채워 넣을 것.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
