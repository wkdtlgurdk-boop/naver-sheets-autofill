#!/usr/bin/env python3
"""수급동향 시트 자동 입력 (외국인/기관 x 코스피/코스닥 순매수 상위 20종목).

네이버 trendForeignOrg 공개 엔드포인트에서 받아 검증한 뒤 Sheets API로 직접 쓴다.
예전 방식(크롬 클립보드 붙여넣기)과 달리 브라우저도 켜져 있는 PC도 필요 없다.

검증에 하나라도 실패하면 아무것도 쓰지 않고 exit 1.

    python supply_demand.py --dry-run    # 수집·검증만, 시트는 안 건드림
    python supply_demand.py              # 실제 기록
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from sheets import Spreadsheet

KST = timezone(timedelta(hours=9))
SPREADSHEET_ID = "1F3SOlj6ER_JUOjrPdKxQeLWbAuHHf49eiB5xyl0rM7U"

BASE = "https://stock.naver.com/api/domestic/market/trend/trendForeignOrg"
HEADERS = {
    "Referer": "https://stock.naver.com/",
    "User-Agent": "Mozilla/5.0 supply-demand-autofill/3.0",
}

# 2026-09-02 개편 후 구조. 이전에는 요일마다 열 수가 달랐고(외인 4열·기관 3열)
# 5종목마다 숨김 3행이 끼어 한 블록이 29행이었다. 지금은 두 시트 모두
# 종목명/금액(백만원) 2열, 숨김 행 없이 20행 연속이라 요일 간격이 균일하다.
DAY_COL = {"MON": "B", "TUE": "D", "WED": "F", "THU": "H", "FRI": "J"}
BLOCK_ROWS = 20
WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI"]

# rows = '금주' 블록의 첫 데이터 행, prev = 같은 블록의 '전주' 첫 데이터 행.
# 두 시트의 간격이 다르다 — 외인 쪽 블록 사이에 빈 행이 하나 더 있다.
# 2026-09-07 실측: 외인 라벨 A28/A74, 기관 A26/A71, 각각 헤더 한 줄 뒤가 데이터.
SHEET = {
    "외인": {"gid": 0, "investor": "FOREIGNER",
             "rows": {"KOSPI": 30, "KOSDAQ": 76},
             "prev": {"KOSPI": 6, "KOSDAQ": 53}},
    "기관": {"gid": 1644270427, "investor": "ORGANIZATION",
             "rows": {"KOSPI": 28, "KOSDAQ": 73},
             "prev": {"KOSPI": 6, "KOSDAQ": 50}},
}
# 요일 5칸 x 2열 = B..K. 회전은 이 폭을 통째로 옮긴다.
SPAN = ("B", "K")


def fetch(investor_type: str, market_type: str) -> list[dict]:
    qs = urllib.parse.urlencode({
        "investorType": investor_type, "tradeType": "KRX",
        "marketType": market_type, "startIdx": 0,
        "pageSize": BLOCK_ROWS, "periodType": "DAY",
    })
    req = urllib.request.Request(f"{BASE}?{qs}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["sections"]["buyRankList"]


def validate(rows: list[dict], today: str, label: str) -> None:
    """확정치가 아니면 중단. 장중 실행·휴장일·데이터 지연을 여기서 전부 막는다.

    장중에는 FOREIGNER 가 estimated=true 에 accTradeAmount=0 인 오늘치를 주고,
    ORGANIZATION 은 아직 어제 확정치를 준다 — 두 종류가 서로 다른 날짜인 채로
    시트에 들어가는 것이 제일 나쁜 결과다.
    """
    if len(rows) < BLOCK_ROWS:
        raise SystemExit(f"[중단] {label}: {BLOCK_ROWS}종목이 안 됨 ({len(rows)}개)")
    top = rows[0]
    if top.get("bizdateFrom") != today:
        raise SystemExit(
            f"[중단] {label}: 데이터 날짜가 오늘이 아님 "
            f"(응답 {top.get('bizdateFrom')}, 오늘 {today}). 휴장일이거나 확정 전."
        )
    if top.get("estimated"):
        raise SystemExit(f"[중단] {label}: 장중 추정치(estimated=true). 마감 후 재실행 필요.")
    if int(top.get("accTradeAmount") or 0) <= 0:
        raise SystemExit(f"[중단] {label}: 금액이 0. 확정 전 데이터.")


def to_values(rows: list[dict]) -> list[list]:
    """종목명 / 금액(백만원) 2열 x 20행."""
    return [[r["itemname"], round(int(r["accTradeAmount"]) / 1e6)]
            for r in rows[:BLOCK_ROWS]]


def rotate(book: Spreadsheet, dry: bool) -> None:
    """월요일: '금주' 블록을 '전주'로 옮기고 '금주'를 비운다.

    로컬판은 이 회전을 위해 매일 쓴 값을 history/ 에 따로 쌓았다 — 클립보드도
    DOM 도 시트를 되읽지 못해서 옮길 원본이 없었기 때문이다. API 로는 그냥
    읽어서 쓰면 되므로 이력 파일도, 그 파일이 없는 날 생기는 구멍도 사라진다.
    """
    c1, c2 = SPAN
    blank = [["", ""] * 5 for _ in range(BLOCK_ROWS)]
    for name, cfg in SHEET.items():
        for market in ("KOSPI", "KOSDAQ"):
            cur, prev = cfg["rows"][market], cfg["prev"][market]
            src = f"{c1}{cur}:{c2}{cur + BLOCK_ROWS - 1}"
            dst = f"{c1}{prev}:{c2}{prev + BLOCK_ROWS - 1}"
            block = book.read(cfg["gid"], src)
            # 읽은 격자는 뒤쪽 빈 칸이 잘려 온다. 폭을 다시 채워 넣지 않으면
            # 전주 칸의 오른쪽 요일이 지난주 값을 그대로 달고 남는다.
            filled = [(row + [""] * 10)[:10] for row in block]
            filled += [[""] * 10] * (BLOCK_ROWS - len(filled))
            print(f"  회전 {name}/{market}: {src} -> {dst} "
                  f"({sum(1 for r in block if r and str(r[0]).strip())}종목)")
            if dry:
                continue
            book.write(cfg["gid"], dst, filled)
            book.write(cfg["gid"], src, blank)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="수집·검증만 하고 시트는 건드리지 않는다")
    a = ap.parse_args()

    now = datetime.now(KST)
    if now.weekday() > 4:
        raise SystemExit(f"[중단] 주말({now:%A})이라 실행하지 않음.")
    today = now.strftime("%Y%m%d")
    weekday = WEEKDAYS[now.weekday()]
    col = DAY_COL[weekday]
    print(f"{now:%Y-%m-%d %H:%M} KST — {weekday} ({col}열)"
          + (" [dry-run]" if a.dry_run else ""))

    # 네 블록을 모두 받아 검증한 뒤에야 쓰기 시작한다. 하나라도 확정 전이면
    # 시트를 반만 채우는 것보다 아무것도 안 쓰는 편이 낫다.
    payload = {}
    for name, cfg in SHEET.items():
        for market in ("KOSPI", "KOSDAQ"):
            label = f"{name}/{market}"
            rows = fetch(cfg["investor"], market)
            validate(rows, today, label)
            payload[(name, market)] = to_values(rows)
            top = rows[0]
            print(f"  {label}: 1위 {top['itemname']} "
                  f"{round(int(top['accTradeAmount']) / 1e6):,}백만")

    book = Spreadsheet(SPREADSHEET_ID)
    if weekday == "MON":
        rotate(book, a.dry_run)

    for (name, market), values in payload.items():
        cfg = SHEET[name]
        r0 = cfg["rows"][market]
        rng = f"{col}{r0}:{chr(ord(col) + 1)}{r0 + BLOCK_ROWS - 1}"
        if a.dry_run:
            print(f"  (dry-run) {name}/{market} -> {rng}")
            continue
        n = book.write(cfg["gid"], rng, values)
        print(f"  {name}/{market} -> {rng} ({n}칸)")

    print("완료" if not a.dry_run else "dry-run 종료 — 시트를 건드리지 않았습니다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
