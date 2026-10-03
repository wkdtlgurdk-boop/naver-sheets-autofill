#!/usr/bin/env python3
"""sheet_check 가 남은 내용과 빠진 연간 탭을 찾는지 본다.

실행: python test_sheet_check.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import sheet_check as sc

KST = timezone(timedelta(hours=9))
failures: list[str] = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    got : {got}\n    want: {want}")


def block(date=""):
    """헤더행 + 대표행 + 종목 다섯 줄. 슬롯 둘(B, E)."""
    return [
        [date, "종목명", "상승률", "거래대금", "종목명", "상승률", "거래대금"],
        ["", "", "0.00%", "0", "", "0.00%", "0"],
        *[[""] * 7 for _ in range(5)],
        [""] * 7, [""] * 7, [""] * 7,
    ]


class Book:
    def __init__(self, grid=None, titles=()):
        self.grid, self._titles = grid or [], list(titles)

    def read_formatted(self, gid, a1):
        return self.grid

    def title_for_gid(self, gid):
        return "[복사해서 쓰시오 지우지마시오]"

    def titles(self):
        return self._titles


def test_clean_template_has_no_leftovers():
    grid = [["Daily Keyword Calendar"], ["일자"]] + block() + block()
    check("빈 템플릿", sc.template_leftovers(grid), [])


def test_stale_date_and_stock_are_found():
    grid = [["Daily Keyword Calendar"], ["일자"]] + block() + block("2/11")
    grid[3][1] = "로봇"       # 첫 블록 대표행 키워드
    grid[5][4] = "브릴스"     # 첫 블록 둘째 슬롯 종목
    check("남은 것 셋", sc.template_leftovers(grid),
          ["B4 에 '로봇'", "E6 에 '브릴스'", "A13 에 '2/11' (날짜 자리)"])


def test_next_year_tab_checked_from_november():
    oct_ = datetime(2026, 10, 30, tzinfo=KST)
    nov = datetime(2026, 11, 2, tzinfo=KST)
    check("10월에는 안 봄", sc.missing_next_year(["2026"], oct_), None)
    check("11월에 없으면", sc.missing_next_year(["2026"], nov), "2027")
    check("11월에 있으면", sc.missing_next_year(["2026", "2027"], nov), None)


def test_check_reports_both():
    daily = Book([["일자"]] + block("2/11"))
    monthly = Book(titles=["2026"])
    out = sc.check(daily, monthly, datetime(2026, 12, 1, tzinfo=KST))
    check("문제 둘", len(out), 2)
    check("템플릿 먼저", "A2 에 '2/11'" in out[0], True)
    check("연간 탭", "'2027'" in out[1], True)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{t.__name__} 예외: {exc!r}")
    if failures:
        print(f"실패 {len(failures)}건 / 전체 {len(tests)}건\n")
        for f in failures:
            print("  " + f)
        return 1
    print(f"통과 {len(tests)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
