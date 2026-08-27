#!/usr/bin/env python3
"""테마 스캔 결과를 Daily / Monthly Keyword Calendar 에 기록한다.

theme_scan.py 가 만든 final_groups.json 과 market_summary.py 의 지수 요약을
읽어 두 시트에 쓴다. 예전에는 이 단계를 LLM 이 크롬 화면을 보며 클립보드로
붙여넣었는데, 블록 위치 탐색이 시각 판단이라 불안정했다. Sheets API 로는
A열과 헤더행을 그대로 읽어서 찾으므로 탐색이 결정적이다.

안전 원칙: 위치를 확신할 수 없으면 아무것도 쓰지 않고 죽는다. 예전 프롬프트의
"블록을 못 찾으면 시트를 건드리지 말고 멈춰라" 규칙을 그대로 따른다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sheets import Spreadsheet

KST = timezone(timedelta(hours=9))

DAILY_ID = "1xq_WNQh04xV1UsLm9ZRL6HhkQIJn-34G41zm87oR2h4"
DAILY_GID = 1762051266
MONTHLY_ID = "1OtZb8hDwb_vK1yGo6StT09jEUytXD9uoxB6Vxmq69ss"
MONTHLY_GID = 1940618236

STOCK_ROWS = 5          # 슬롯당 기록하는 종목 수
MONTHLY_MAX_GROUPS = 6  # 월간 시트 한 날짜 블록에 들어가는 키워드 행 수
SEARCH_ROWS = 600       # 달력 탭에서 훑어볼 행 범위

# 월간 시트: 요일별 3열 묶음.
MONTHLY_COLS = {
    0: ("B", "C", "D"), 1: ("E", "F", "G"), 2: ("H", "I", "J"),
    3: ("K", "L", "M"), 4: ("N", "O", "P"),
}


def col_letter(idx: int) -> str:
    """0-based 열 인덱스를 A1 표기로."""
    out = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def find_day_row(book: Spreadsheet, gid: int, column: str, wanted: str) -> int:
    """지정한 열에서 표시값이 wanted 인 행을 찾는다. 유일해야 한다.

    병합된 날짜 셀은 좌상단 한 칸에만 값이 실려 오므로 그 칸이 곧 블록의 첫
    행이다. 후보가 0개면 그 주 블록이 아직 없는 것이고, 2개 이상이면 (분기
    탭에 같은 일자가 여러 번 나오는 경우) 사람이 판단해야 한다.
    """
    rows = book.read_formatted(gid, f"{column}1:{column}{SEARCH_ROWS}")
    hits = [i + 1 for i, r in enumerate(rows) if r and str(r[0]).strip() == wanted]
    if not hits:
        raise SystemExit(
            f"[중단] {column}열에서 '{wanted}' 를 찾지 못했습니다. "
            "이번 주(또는 이번 달) 블록이 아직 만들어지지 않았습니다. "
            "시트를 건드리지 않고 종료합니다."
        )
    if len(hits) > 1:
        raise SystemExit(
            f"[중단] {column}열에 '{wanted}' 가 {len(hits)}곳 있습니다 (행 {hits}). "
            "어느 블록인지 자동으로 정할 수 없습니다."
        )
    return hits[0]


def write_daily(book: Spreadsheet, groups: list[dict], now: datetime) -> list[dict]:
    """Daily Keyword Calendar 에 오늘 블록을 채우고, 대표행 계산값을 돌려준다."""
    wanted = f"{now.month}/{now.day}"
    header_row = find_day_row(book, DAILY_GID, "A", wanted)

    # 헤더행에서 슬롯 위치를 읽는다. 슬롯 수는 주마다 다를 수 있어(기본 6개,
    # 필요하면 사용자가 오른쪽으로 늘려둠) 고정하지 않는다.
    header = book.read_formatted(DAILY_GID, f"A{header_row}:Z{header_row}")
    header = header[0] if header else []
    slots = [i for i, v in enumerate(header) if str(v).strip() == "종목명"]
    if not slots:
        raise SystemExit(
            f"[중단] {header_row}행이 헤더행이 아닙니다 ('종목명' 없음). "
            f"A{header_row}='{wanted}' 이지만 블록 구조가 예상과 다릅니다."
        )
    print(f"  Daily: A{header_row}='{wanted}', 슬롯 {len(slots)}개 "
          f"({', '.join(col_letter(s) for s in slots)})")

    # 슬롯별 현재 상태를 먼저 본다. 하루 전체를 한 칸으로 판단하면 안 된다 —
    # 20:10 로컬 작업(과 이 스크립트)은 슬롯을 하나씩 쓰므로 도중에 죽으면
    # 하루가 반만 찬 채로 남는다. 첫 칸만 보고 "이미 기록됨"으로 건너뛰면
    # 나머지 슬롯이 영영 비어 있게 된다.
    kw_row = book.read_formatted(DAILY_GID, f"A{header_row + 1}:Z{header_row + 1}")
    kw_row = kw_row[0] if kw_row else []

    def keyword_at(slot: int) -> str:
        return str(kw_row[slot]).strip() if slot < len(kw_row) else ""

    taken = {keyword_at(s): s for s in slots if keyword_at(s)}
    free = [s for s in slots if not keyword_at(s)]
    if taken:
        print(f"  Daily: 이미 채워진 슬롯 {len(taken)}개 — "
              f"{', '.join(f'{k}@{col_letter(v)}' for k, v in taken.items())}")

    # 같은 키워드가 이미 자리를 잡고 있으면 그 자리를 쓰고, 아니면 빈 자리에
    # 넣는다. 남의 키워드가 든 슬롯은 절대 덮어쓰지 않는다.
    plan, dropped = [], []
    for g in groups:
        if g["keyword"] in taken:
            plan.append((g, taken[g["keyword"]]))
        elif free:
            plan.append((g, free.pop(0)))
        else:
            dropped.append(g["keyword"])
    if dropped:
        print(f"  Daily: 빈 슬롯이 없어 {len(dropped)}개 제외 — {', '.join(dropped)}")

    for g, slot in plan:
        c1, c2, c3 = col_letter(slot), col_letter(slot + 1), col_letter(slot + 2)

        # 이 슬롯이 이미 온전히 채워져 있으면 건너뛴다. '온전히'는 이 그룹이
        # 쓸 종목 수만큼 종목명/상승률/거래대금이 모두 있다는 뜻이다. 반만
        # 찬 슬롯은 다시 쓴다 — 같은 값을 덮는 것이므로 무해하다.
        want = len(g["top5"][:STOCK_ROWS])
        block = book.read(DAILY_GID,
                          f"{c1}{header_row + 1}:{c3}{header_row + 1 + want}")

        def filled(row_idx: int) -> bool:
            row = block[row_idx] if row_idx < len(block) else []
            return len(row) >= 3 and all(str(v).strip() for v in row[:3])

        if keyword_at(slot) == g["keyword"] and all(filled(i) for i in range(1, want + 1)):
            print(f"    [{g['keyword']}] {c1}{header_row + 1} 이미 완전히 채워짐 — 건너뜀")
            continue

        # 대표행(header_row+1)의 상승률/거래대금 칸에는 AVERAGEA/SUM 수식이
        # 들어 있다. 종목명 칸만 쓰고 나머지 두 칸은 절대 건드리지 않는다.
        formulas = book.read_formulas(DAILY_GID, f"{c2}{header_row + 1}:{c3}{header_row + 1}")
        cells = formulas[0] if formulas else []
        if not (len(cells) >= 2
                and str(cells[0]).startswith("=") and str(cells[1]).startswith("=")):
            raise SystemExit(
                f"[중단] {c2}{header_row + 1}:{c3}{header_row + 1} 에 수식이 없습니다 "
                f"(읽은 값: {cells}). 템플릿이 준비되지 않은 블록입니다."
            )

        top = g["top5"][:STOCK_ROWS]
        names = [g["keyword"]] + [s["name"] for s in top]
        # 상승률은 '12.34%' 문자열로 보낸다. 이 열은 백분율 서식이라 숫자
        # 12.34 를 넣으면 1234% 가 된다 — 클립보드 시절과 같은 표기를 유지한다.
        rates = [[f"{s['rate']:.2f}%"] for s in top]
        amounts = [[int(s["amount"])] for s in top]

        book.write(DAILY_GID, f"{c1}{header_row + 1}:{c1}{header_row + len(names)}",
                   [[n] for n in names])
        book.write(DAILY_GID, f"{c2}{header_row + 2}:{c2}{header_row + 1 + len(rates)}", rates)
        book.write(DAILY_GID, f"{c3}{header_row + 2}:{c3}{header_row + 1 + len(amounts)}", amounts)
        print(f"    [{g['keyword']}] -> {c1}{header_row + 1} "
              f"({len(top)}종목, 1위 {top[0]['name']} {top[0]['rate']:+.2f}%)")

    return read_daily_totals(book, header_row, slots)


def read_daily_totals(book: Spreadsheet, header_row: int,
                      slots: list[int]) -> list[dict]:
    """대표행이 계산한 평균상승률/거래대금합을 시트에서 그대로 읽어온다.

    월간 시트에 이 값을 옮겨 적어야 하는데, 수식 의미를 파이썬에서 다시
    구현하면 언젠가 어긋난다. 시트가 계산한 값을 읽는 편이 항상 일치한다.
    """
    out = []
    for slot in slots:
        c1 = col_letter(slot)
        c3 = col_letter(slot + 2)
        row = book.read(DAILY_GID, f"{c1}{header_row + 1}:{c3}{header_row + 1}")
        cells = row[0] if row else []
        if len(cells) < 3 or not str(cells[0]).strip():
            continue
        out.append({"keyword": cells[0], "rate": cells[1], "amount": cells[2]})
    return out


def write_monthly(book: Spreadsheet, totals: list[dict], market: dict,
                  now: datetime) -> None:
    c1, c2, c3 = MONTHLY_COLS[now.weekday()]
    day_row = find_day_row(book, MONTHLY_GID, c1, str(now.day))
    print(f"  Monthly: {c1}{day_row}='{now.day}' ({c1}/{c2}/{c3} 열)")

    kospi, kosdaq = market["KOSPI"], market["KOSDAQ"]
    book.write(MONTHLY_GID, f"{c2}{day_row}:{c3}{day_row}",
               [[f"코스피 {round(kospi['index'])}", f"코스닥 {round(kosdaq['index'])}"]])
    book.write(MONTHLY_GID, f"{c2}{day_row + 1}:{c3}{day_row + 1}",
               [[kospi["amount_trillion"], kosdaq["amount_trillion"]]])
    book.write(MONTHLY_GID, f"{c2}{day_row + 2}:{c3}{day_row + 2}",
               [[kospi["foreign_eok"], kosdaq["foreign_eok"]]])
    print(f"    지수 {round(kospi['index'])}/{round(kosdaq['index'])}, "
          f"거래대금 {kospi['amount_trillion']}/{kosdaq['amount_trillion']}조, "
          f"외인 {kospi['foreign_eok']}/{kosdaq['foreign_eok']}억")

    # day_row+3 은 '평균상승률 / 거래대금합' 라벨 행이므로 건드리지 않는다.
    rows = [[t["keyword"], t["rate"], t["amount"]] for t in totals[:MONTHLY_MAX_GROUPS]]
    if len(totals) > MONTHLY_MAX_GROUPS:
        dropped = [str(t["keyword"]) for t in totals[MONTHLY_MAX_GROUPS:]]
        print(f"    월간 블록은 {MONTHLY_MAX_GROUPS}행까지라 제외 — {', '.join(dropped)}")
    start = day_row + 4
    book.write(MONTHLY_GID, f"{c1}{start}:{c3}{start + len(rows) - 1}", rows)
    print(f"    키워드 {len(rows)}행 기록 ({c1}{start} 부터)")


def main() -> int:
    now = datetime.now(KST)
    if now.weekday() > 4:
        raise SystemExit(f"[중단] 주말({now:%A})이라 실행하지 않음.")

    here = Path(__file__).parent
    groups = json.loads((here / "final_groups.json").read_text(encoding="utf-8"))
    market = json.loads((here / "market_summary.json").read_text(encoding="utf-8"))
    if not groups:
        raise SystemExit("[중단] 조건을 만족하는 테마 그룹이 0개입니다. 시트를 건드리지 않습니다.")
    print(f"{now:%Y-%m-%d %H:%M} KST — 그룹 {len(groups)}개")

    daily = Spreadsheet(DAILY_ID)
    totals = write_daily(daily, groups, now)

    monthly = Spreadsheet(MONTHLY_ID)
    write_monthly(monthly, totals, market, now)

    print("완료 — 두 시트 모두 기록됨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
