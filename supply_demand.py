#!/usr/bin/env python3
"""수급동향 시트 자동 입력 (외국인/기관 x 코스피/코스닥 순매수 상위 20종목).

네이버 trendForeignOrg 공개 엔드포인트에서 받아 검증한 뒤 Sheets API로 직접 쓴다.
예전 방식(크롬 클립보드 붙여넣기)과 달리 브라우저도 켜져 있는 PC도 필요 없다.

검증에 하나라도 실패하면 아무것도 쓰지 않고 exit 1.

주의 — 이 스크립트는 예약 실행되지 않는다. 수급동향 시트는 별도의 Google Apps
Script 가 담당하기로 정리됐고(같은 셀에 두 시스템이 쓰면 깨진다), 그래서 이
리포의 워크플로 중 어느 것도 이 파일을 부르지 않는다. Apps Script 쪽이 끝내
동작하지 않을 때 쓸 수 있도록 남겨둔 대안이며, 되살리려면 워크플로를 새로
추가하고 그 전에 Apps Script 트리거를 반드시 먼저 꺼야 한다.
"""
from __future__ import annotations

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
    "User-Agent": "Mozilla/5.0 supply-demand-autofill/2.0",
    "Accept": "application/json",
}

# 요일별 '종목' 열 시작 위치.
# 외인 시트는 종목/수량/금액/거래량 4열, 기관 시트는 종목/(빈칸)/금액 3열을 쓴다.
DAY_COL = {
    "외인": {"MON": "B", "TUE": "F", "WED": "J", "THU": "O", "FRI": "S"},
    "기관": {"MON": "B", "TUE": "G", "WED": "K", "THU": "O", "FRI": "S"},
}
SHEET = {
    "외인": {"gid": 0, "cols": 4, "rows": {"KOSPI": 39, "KOSDAQ": 112}},
    "기관": {"gid": 1644270427, "cols": 3, "rows": {"KOSPI": 38, "KOSDAQ": 111}},
}
INVESTOR = {"외인": "FOREIGNER", "기관": "ORGANIZATION"}
WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI"]


def fetch(investor_type: str, market_type: str) -> list[dict]:
    qs = urllib.parse.urlencode({
        "investorType": investor_type,
        "tradeType": "KRX",
        "marketType": market_type,
        "startIdx": 0,
        "pageSize": 20,
        "periodType": "DAY",
    })
    req = urllib.request.Request(f"{BASE}?{qs}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        import json
        data = json.load(resp)
    return data["sections"]["buyRankList"]


def validate(rows: list[dict], today: str, label: str) -> None:
    """확정치가 아니면 중단. 장중 실행/휴장일/데이터 지연을 여기서 전부 막는다."""
    if len(rows) < 20:
        raise SystemExit(f"[중단] {label}: 20종목이 안 됨 ({len(rows)}개)")
    top = rows[0]
    if top.get("bizdateFrom") != today:
        raise SystemExit(
            f"[중단] {label}: 데이터 날짜가 오늘이 아님 "
            f"(응답 {top.get('bizdateFrom')}, 오늘 {today}). 휴장일이거나 아직 확정 전."
        )
    if top.get("estimated"):
        raise SystemExit(f"[중단] {label}: 장중 추정치(estimated=true). 마감 후 재실행 필요.")
    if int(top.get("accTradeAmount") or 0) <= 0:
        raise SystemExit(f"[중단] {label}: 금액이 0. 확정 전 데이터.")


def build_rows(rows: list[dict], cols: int) -> list[list]:
    """5행 + 숨김 3행 패턴으로 29행 블록 생성 (20종목).

    숫자는 문자열이 아니라 수로 넣는다 — 클립보드 시절엔 '1,234' 같은 서식 문자열을
    시트가 파싱해줬지만, API로는 수를 그대로 보내는 편이 로케일에 휘둘리지 않는다.
    """
    blank = [""] * cols
    out: list[list] = []
    for i, r in enumerate(rows[:20]):
        amount = round(int(r["accTradeAmount"]) / 1e6)   # 백만원
        volume = round(int(r["accTradeVolume"]) / 1000)  # 천주
        if cols == 4:
            out.append([r["itemname"], volume, amount, int(r["dailyTradeVolume"])])
        else:
            out.append([r["itemname"], "", amount])
        if i % 5 == 4 and i != 19:
            out.extend([list(blank) for _ in range(3)])
    return out


def col_shift(col: str, n: int) -> str:
    """단일 문자 열 이름을 n칸 옮긴다 (이 시트들은 전부 A~Z 범위)."""
    idx = ord(col) - ord("A") + n
    if idx > 25:
        raise SystemExit(f"[중단] 열 범위를 벗어남: {col}+{n}")
    return chr(ord("A") + idx)


def main() -> int:
    now = datetime.now(KST)
    today = now.strftime("%Y%m%d")
    if now.weekday() > 4:
        raise SystemExit(f"[중단] 주말({now:%A})이라 실행하지 않음.")
    weekday = WEEKDAYS[now.weekday()]

    # 네트워크 수집과 검증을 먼저 전부 끝낸다. 하나라도 어긋나면
    # 시트를 한 칸도 건드리지 않은 상태로 죽는 편이 낫다.
    blocks = []
    for sheet_name in ("외인", "기관"):
        cfg = SHEET[sheet_name]
        col = DAY_COL[sheet_name][weekday]
        for market in ("KOSPI", "KOSDAQ"):
            label = f"{sheet_name}/{market}"
            rows = fetch(INVESTOR[sheet_name], market)
            validate(rows, today, label)
            start_row = cfg["rows"][market]
            values = build_rows(rows, cfg["cols"])
            end_col = col_shift(col, cfg["cols"] - 1)
            blocks.append({
                "label": label,
                "gid": cfg["gid"],
                "anchor": f"{col}{start_row}",
                "range": f"{col}{start_row}:{end_col}{start_row + len(values) - 1}",
                "top_name": rows[0]["itemname"],
                "values": values,
            })

    print(f"수집 완료 {today} ({weekday}) — {len(blocks)}개 블록")

    book = Spreadsheet(SPREADSHEET_ID)
    written = skipped = 0
    for b in blocks:
        existing = book.read(b["gid"], b["anchor"])
        current = existing[0][0] if existing and existing[0] else ""
        if current and str(current).strip() == b["top_name"]:
            print(f"  {b['label']:12s} {b['range']:12s} 이미 채워져 있음 (1위={current}) — 건너뜀")
            skipped += 1
            continue
        if current:
            raise SystemExit(
                f"[중단] {b['label']}: 앵커 {b['anchor']} 에 예상과 다른 값 '{current}' "
                f"(오늘 1위는 '{b['top_name']}'). 요일/행 계산이 틀렸거나 수동 입력이 있습니다."
            )
        cells = book.write(b["gid"], b["range"], b["values"])
        print(f"  {b['label']:12s} {b['range']:12s} {cells}칸 기록 (1위={b['top_name']})")
        written += 1

    print(f"완료 — 기록 {written}개 블록, 건너뜀 {skipped}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
