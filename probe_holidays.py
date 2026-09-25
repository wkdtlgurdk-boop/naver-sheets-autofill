#!/usr/bin/env python3
"""일회용 진단. 휴장일 달력을 무엇으로 채울지 러너가 확인한다.

## 왜 손으로 안 적나

설날과 추석은 음력이라 해마다 옮겨 다니고, 대체공휴일 규칙도 바뀌었으며,
임시공휴일은 연중에 발표된다. 기억으로 적으면 하루만 틀려도 그날 알림이
통째로 빠지거나 휴장일에 헛알림이 나간다. 자료로 만든다.

## 두 갈래를 확인한다

1. **네이버가 아는 실제 거래일.** /api/index/KOSPI/price 가 장이 선 날짜만
   준다. 지난 날짜에 대해서는 이것이 가장 정확한 출처다 -- 임시공휴일이든
   태풍 휴장이든 결과가 그대로 찍혀 있다. 다만 앞날은 모른다.

2. **공휴일 API.** 앞날을 알려면 달력이 필요하다. holidayapi.kr 은 열쇠가
   없어도 쓸 수 있다고 알려져 있는데 확인한 적이 없다. 살아 있는지, 응답이
   어떻게 생겼는지 본다.

살아 있는 쪽으로 달력을 만들고, 1번으로 과거 구간을 맞춰 본다. 둘이 어긋나는
날이 있으면 그 날이 바로 손으로 봐야 할 날이다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, timedelta

NAVER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://m.stock.naver.com/",
    "User-Agent": "Mozilla/5.0 supply-demand-autofill/4.0",
}
PRICE = "https://m.stock.naver.com/api/index/{market}/price?pageSize={size}&page={page}"


def get(url: str, headers: dict | None = None, timeout: int = 20):
    """(HTTP 코드, 본문, 메모). 죽지 않는다 -- 실패도 답이다."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw), ""
            except json.JSONDecodeError:
                return resp.status, None, f"JSON 아님 (앞 120자: {raw[:120]!r})"
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.reason or ""
    except Exception as exc:                       # noqa: BLE001 - 진단용
        return 0, None, f"{type(exc).__name__}: {exc}"


def trading_days(market: str, pages: int = 8, size: int = 60) -> list[str]:
    """실제로 장이 선 날짜(YYYY-MM-DD). 오래된 것이 앞에 온다.

    pageSize 상한이 있다 -- 100 은 400 을 낸다(2026-09-25 실측). 60 으로
    끊어 여러 장을 받는다. 종목별 trend 경로도 같은 상한이었다.
    """
    out: set[str] = set()
    for page in range(1, pages + 1):
        code, doc, note = get(PRICE.format(market=market, size=size, page=page),
                              NAVER_HEADERS)
        if not isinstance(doc, list) or not doc:
            print(f"    page={page} HTTP {code} {note} -- 여기서 끊는다")
            break
        for row in doc:
            day = str((row or {}).get("localTradedAt") or "")
            if len(day) == 10:
                out.add(day)
    return sorted(out)


def weekday_gaps(days: list[str]) -> list[str]:
    """거래일 목록의 처음과 끝 사이에서 '평일인데 장이 안 선 날'을 뽑는다.

    주말은 뺀다. 남는 것이 곧 휴장일이다 -- 공휴일이든 임시공휴일이든
    이유를 묻지 않고 결과만 본다.
    """
    if len(days) < 2:
        return []
    have = set(days)
    start = date.fromisoformat(days[0])
    end = date.fromisoformat(days[-1])
    out, cur = [], start
    while cur <= end:
        if cur.weekday() < 5 and cur.isoformat() not in have:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def probe_market() -> list[str]:
    print("=" * 70)
    print("1. 네이버가 아는 실제 거래일 (지난 날짜에는 이것이 가장 정확하다)")
    print("=" * 70)
    days = trading_days("KOSPI")
    if not days:
        print("  받지 못했다.")
        return []
    print(f"  거래일 {len(days)}개: {days[0]} ~ {days[-1]}")
    print(f"  최근 10개: {days[-10:]}")
    gaps = weekday_gaps(days)
    print(f"\n  이 구간에서 평일인데 장이 안 선 날 {len(gaps)}개:")
    for day in gaps:
        name = ["월", "화", "수", "목", "금"][date.fromisoformat(day).weekday()]
        print(f"    {day} ({name})")
    today = date.today().isoformat()
    print(f"\n  오늘({today})이 거래일 목록에 있는가: "
          f"{'있다 -- 장이 섰다' if today in set(days) else '없다'}")
    return gaps


def probe_holiday_api() -> None:
    print("\n" + "=" * 70)
    print("2. 공휴일 API (앞날을 알려면 이쪽이 필요하다)")
    print("=" * 70)
    year = date.today().year
    for url in (f"https://holidayapi.kr/holidays/{year}",
                f"https://holidayapi.kr/holidays/{year + 1}",
                f"https://date.nager.at/api/v3/PublicHolidays/{year}/KR",
                f"https://date.nager.at/api/v3/PublicHolidays/{year + 1}/KR"):
        code, doc, note = get(url, {"User-Agent": "Mozilla/5.0 holiday-probe/1.0"})
        print(f"\n--- {url}")
        print(f"    HTTP {code}{('  ' + note) if note else ''}")
        if doc is None:
            continue
        rows = doc.get("data") if isinstance(doc, dict) else doc
        if not isinstance(rows, list):
            print(f"    모양이 예상과 다르다: {str(doc)[:200]}")
            continue
        print(f"    {len(rows)}건. 첫 원소: {json.dumps(rows[0], ensure_ascii=False)}")
        got = []
        for row in rows:
            day = str(row.get("date") or row.get("locdate") or "")
            name = str(row.get("name") or row.get("localName") or "")
            if day:
                got.append(f"{day[:10]} {name}")
        print("    전체:")
        for line in got:
            print(f"      {line}")


def main() -> int:
    gaps = probe_market()
    probe_holiday_api()
    print("\n" + "=" * 70)
    print("맞춰 보는 법: 공휴일 API 목록에서 주말을 뺀 평일 공휴일이 1번의")
    print("'평일인데 장이 안 선 날'과 같아야 한다. 어긋나는 날이 있으면")
    print("(근로자의날 5/1, 연말휴장 12/31, 임시공휴일) 손으로 채워야 한다.")
    if gaps:
        print(f"\n1번이 찾은 휴장일: {gaps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
