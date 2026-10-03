#!/usr/bin/env python3
"""달력 시트가 다음 실행을 받을 준비가 됐는지 본다. 문제는 사람이 고쳐야 하는 것들이다.

    python sheet_check.py              # 문제가 있으면 줄마다 찍고 종료코드 1
    python sheet_check.py --out x.md   # 같은 내용을 이슈 본문용 마크다운으로도 쓴다

## 무엇을 보는가

1. Daily 템플릿 탭에 지난 내용이 남아 있지 않은가. 새 달 탭은 템플릿을 통째로
   복사해 만든다. 2026-10-01 에 템플릿 A열에 남은 '2/11' 이 10월 탭으로 그대로
   넘어가 10/1 이 A85 에 적혔다. theme_write 는 이제 그런 날짜를 무시하지만,
   종목·키워드까지 남아 있으면 그 블록을 '찬 블록'으로 보고 건너뛴다.
2. 11월부터는 내년 Monthly 연간 탭이 있는가. 연간 탭은 병합·서식이 많아
   자동으로 만들지 않는다(theme_write.resolve_monthly_gid). 없으면 1월 첫
   거래일 실행이 [중단] 으로 멈춘다 — 두 달 앞서 알린다.

워크플로는 문제가 있으면 GitHub 이슈를 연다(같은 제목이 열려 있으면 새로
열지 않는다). 실행 로그를 따로 찾아보지 않아도 알림이 오게 하려는 것이다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from as_of import as_of
from theme_write import DAILY_ID, DAILY_TEMPLATE_GID, MONTHLY_ID, SEARCH_ROWS

HEADER_MARK = "종목명"
WARN_FROM_MONTH = 11   # 내년 연간 탭을 이 달부터 확인한다


def template_leftovers(grid: list[list]) -> list[str]:
    """템플릿 블록에 남은 날짜·키워드·종목. 비어 있어야 정상이다.

    헤더행(종목명 행)의 A열이 날짜 자리, 그 아래 여섯 줄이 키워드·종목 자리다.
    대표행의 상승률·거래대금 칸에는 수식이 있으므로 '종목명' 열만 본다.
    """
    def cell(r: int, c: int) -> str:
        row = grid[r - 1] if 0 < r <= len(grid) else []
        return str(row[c]).strip() if c < len(row) else ""

    found = []
    for r in range(1, len(grid) + 1):
        row = grid[r - 1] or []
        slots = [c for c, v in enumerate(row) if str(v).strip() == HEADER_MARK]
        if not slots:
            continue
        if cell(r, 0):
            found.append(f"A{r} 에 '{cell(r, 0)}' (날짜 자리)")
        for c in slots:
            for rr in range(r + 1, r + 7):
                v = cell(rr, c)
                if v and v != HEADER_MARK:
                    found.append(f"{_col(c)}{rr} 에 '{v}'")
    return found


def _col(idx: int) -> str:
    out, idx = "", idx + 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def missing_next_year(titles: list[str], now: datetime) -> str | None:
    """11월부터 내년 연간 탭이 없으면 그 이름."""
    if now.month < WARN_FROM_MONTH:
        return None
    want = str(now.year + 1)
    return None if want in titles else want


def check(daily, monthly, now: datetime) -> list[str]:
    problems = []
    tpl = daily.read_formatted(DAILY_TEMPLATE_GID, f"A1:Z{SEARCH_ROWS}")
    left = template_leftovers(tpl)
    if left:
        name = daily.title_for_gid(DAILY_TEMPLATE_GID)
        shown = ", ".join(left[:8]) + (f" 외 {len(left) - 8}곳" if len(left) > 8 else "")
        problems.append(
            f"Daily 템플릿 탭 '{name}' 에 지난 내용이 남아 있습니다: {shown}. "
            "다음 달 탭이 이 탭을 복사해 만들어지므로 지워 주세요.")
    year = missing_next_year(monthly.titles(), now)
    if year:
        problems.append(
            f"Monthly 시트에 '{year}' 연간 탭이 없습니다. 1월 첫 거래일 실행이 "
            f"멈춥니다 — '{now.year}' 탭을 복사해 '{year}' 로 이름을 바꾸고 날짜를 "
            "새해 달력에 맞춰 주세요.")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="문제가 있으면 이슈 본문 마크다운을 여기에 쓴다")
    args = ap.parse_args()

    from sheets import Spreadsheet

    now, _ = as_of()
    problems = check(Spreadsheet(DAILY_ID), Spreadsheet(MONTHLY_ID), now)
    if not problems:
        print("시트 점검: 문제 없음")
        return 0
    for p in problems:
        print(f"::warning::{p}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(f"{now:%Y-%m-%d} 테마 분석 실행이 시트에서 사람이 고쳐야 할 것을 찾았습니다.\n\n")
            f.writelines(f"- [ ] {p}\n" for p in problems)
            f.write("\n고친 뒤 이 이슈를 닫아 주세요. 다음 실행이 다시 확인합니다.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
