#!/usr/bin/env python3
"""시장 전체 외인·기관 순매수(코스피·코스닥)와 그 한 줄 평 (2026-10-28 사용자 지정).

수급정리 스레드 본문에 '오늘 외인이 샀다, 기관이 많이 팔았다, 순매수로 돌아섰다' 같은
한 줄을 칸마다 단다. 카드의 순매수 상위 10종목과 달리 이 값은 **시장 전체 합계**라
부호가 있다 -- 팔았는지 샀는지를 말할 수 있다.

    python market_flow.py YYYYMMDD     # market_flow.json 을 쓴다 (supply-demand 워크플로)

출처: m.stock.naver.com/api/index/{KOSPI|KOSDAQ}/trend?bizdate=YYYYMMDD (억원, 부호 있음).
장이 안 선 날은 세 값이 모두 0 으로 온다 -- 그 날은 건너뛴다.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

URL = "https://m.stock.naver.com/api/index/{market}/trend?bizdate={day}"
FLOW_FILE = "market_flow.json"
MARKETS = (("KOSPI", "코스피"), ("KOSDAQ", "코스닥"))
WHO = (("foreignValue", "외인"), ("institutionalValue", "기관"))
HISTORY = 20          # 평소 규모를 잴 거래일 수
BIG, SMALL = 2.0, 0.5  # 평소 대비 이 배수 이상이면 '많이', 이하면 '조금'


def _num(text) -> int:
    try:
        return int(str(text).replace(",", "").replace("+", ""))
    except ValueError:
        return 0


def fetch_day(market: str, day: str) -> dict | None:
    req = urllib.request.Request(URL.format(market=market, day=day),
                                 headers={"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"})
    with urllib.request.urlopen(req, timeout=15) as r:
        got = json.load(r)
    vals = {k: _num(got.get(k)) for k, _ in WHO}
    if not any(vals.values()) and not _num(got.get("personalValue")):
        return None                                   # 휴장일
    return {"date": str(got.get("bizdate") or day), **vals}


def history(market: str, day: str, n: int = HISTORY + 1, fetch=fetch_day) -> list[dict]:
    """day 포함 최근 n 거래일, 최신이 뒤."""
    out, d = [], datetime.strptime(day, "%Y%m%d").date()
    tries = 0
    while len(out) < n and tries < n * 2 + 10:
        tries += 1
        if d.weekday() < 5:
            row = fetch(market, d.strftime("%Y%m%d"))
            if row:
                out.append(row)
        d -= timedelta(days=1)
    return out[::-1]


def won(eok: int) -> str:
    a = abs(eok)
    return f"{a / 10000:.1f}조" if a >= 10000 else f"{a:,}억"


def verdict(values: list[int]) -> str:
    """최근 거래일 순매수(억원, 오래된 것부터, 마지막이 오늘)로 한 줄 평."""
    if not values:
        return "자료 없음"
    today = values[-1]
    if today == 0:
        return "거의 안 움직였다"
    side = "샀다" if today > 0 else "팔았다"
    past = values[:-1]
    usual = sum(abs(v) for v in past[-HISTORY:]) / len(past[-HISTORY:]) if past else 0
    size = ""
    if usual:
        ratio = abs(today) / usual
        size = "많이 " if ratio >= BIG else ("적게 " if ratio <= SMALL else "")
    # 같은 방향이 며칠째인지 / 바로 전날과 반대면 전환
    streak = 1
    for v in reversed(past):
        if (v > 0) == (today > 0) and v != 0:
            streak += 1
        else:
            break
    tail = ""
    if past and (past[-1] > 0) != (today > 0) and past[-1] != 0:
        opp = 0
        for v in reversed(past):
            if (v > 0) != (today > 0) and v != 0:
                opp += 1
            else:
                break
        tail = f" · {opp}일 만에 순{'매수' if today > 0 else '매도'} 전환"
    elif streak >= 3:
        tail = f" · {streak}일 연속 순{'매수' if today > 0 else '매도'}"
    return f"{size}{side} ({'+' if today > 0 else '-'}{won(today)}){tail}"


def lines(flow: dict) -> list[str]:
    """market_flow.json -> 본문 줄들. '코스피 외인이 많이 샀다 (+7,677억) · 3일 만에 순매수 전환'."""
    out = []
    for market, label in MARKETS:
        rows = flow.get(market) or []
        for key, who in WHO:
            vals = [r[key] for r in rows]
            if vals:
                out.append(f"{label} {who}이 {verdict(vals)}")
    return out


def load(here: Path) -> dict:
    p = Path(here) / FLOW_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime("%Y%m%d")
    flow = {}
    for market, _ in MARKETS:
        rows = history(market, day)
        if not rows or rows[-1]["date"] != day:
            print(f"[시장 수급] {market} {day} 자료가 없습니다 — 한 줄 평 없이 올립니다", file=sys.stderr)
            continue
        flow[market] = rows
    Path(__file__).with_name(FLOW_FILE).write_text(json.dumps(flow, ensure_ascii=False, indent=1), encoding="utf-8")
    for line in lines(flow):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
