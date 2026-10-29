#!/usr/bin/env python3
"""시장 전체 외인·기관 순매수(코스피·코스닥)와 그 한 문장 (2026-10-28 사용자 지정).

수급정리 스레드 본문에 '오늘 코스피는 외인은 순매수, 기관은 순매도 했다개' 같은
평문을 시장마다 한 줄 단다. 카드의 순매수 상위 10종목과 달리 이 값은 **시장 전체 합계**라
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


def history(market: str, day: str, n: int = 1, fetch=fetch_day) -> list[dict]:
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
    return f"{'+' if eok > 0 else '-' if eok < 0 else ''}{a / 10000:.1f}조" if a >= 10000 else f"{eok:+,}억"


def side(eok: int) -> str:
    return "순매수" if eok > 0 else "순매도" if eok < 0 else "보합"


def sentence(label: str, foreign: int, inst: int) -> str:
    """'오늘 코스피는 외인은 순매수, 기관은 순매도 했다개 (외인 +5,826억 · 기관 -2,824억)'.

    2026-10-28 사용자 지정: 많이·적게·연속 같은 말 없이 평문 한 문장. 금액은 아침 시그널
    봇의 [수급] 줄처럼 괄호에 붙인다.
    """
    josa = "은" if (ord(label[-1]) - 0xAC00) % 28 else "는"     # 코스닥은 / 코스피는
    f, o = side(foreign), side(inst)
    if f == o:
        body = f"외인·기관 모두 {f}"
    else:
        body = f"외인은 {f}, 기관은 {o}"
    return f"오늘 {label}{josa} {body} 했다개 (외인 {won(foreign)} · 기관 {won(inst)})"


MARK = {"순매수": "🔴", "순매도": "🔵", "보합": "⚪"}


def block(label: str, foreign: int, inst: int) -> list[str]:
    """2026-10-29 사용자 지정: 한 문장은 읽기 어려워 시장 이름 한 줄 + 외인·기관 한 줄씩.

    [코스피]
    외인 🔴 순매수 +5,826억
    기관 🔵 순매도 -2,824억
    """
    return [f"[{label}]"] + [f"{who} {MARK[side(v)]} {side(v)} {won(v)}"
                             for who, v in (("외인", foreign), ("기관", inst))]


def lines(flow: dict) -> list[str]:
    """market_flow.json -> 시장마다 세 줄, 시장 사이는 빈 줄 (오늘 값만 쓴다)."""
    out: list[str] = []
    for market, label in MARKETS:
        rows = flow.get(market) or []
        if rows:
            out += ([""] if out else []) + block(label, rows[-1]["foreignValue"], rows[-1]["institutionalValue"])
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
