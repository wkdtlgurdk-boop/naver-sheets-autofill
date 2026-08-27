#!/usr/bin/env python3
"""theme_write 의 행/열 계산을 가짜 시트로 검증한다.

실제 시트에 붙기 전에는 여기가 유일한 안전망이다. 이 코드에서 제일 위험한 건
네트워크도 인증도 아니라 오프바이원이다 — 대표행 한 줄을 잘못 세면 AVERAGEA
수식을 종목명으로 덮어쓴다. 그래서 '무엇을 어느 범위에 썼는가'를 통째로 비교한다.

실행: python test_theme_write.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone

import theme_write as tw

KST = timezone(timedelta(hours=9))
CELL = re.compile(r"^([A-Z]+)([0-9]+)$")


def parse_a1(a1: str):
    part = a1.split(":")
    start = CELL.match(part[0])
    end = CELL.match(part[1]) if len(part) > 1 else start
    if not start or not end:
        raise AssertionError(f"범위 파싱 실패: {a1}")
    return (col_idx(start.group(1)), int(start.group(2)),
            col_idx(end.group(1)), int(end.group(2)))


def col_idx(letter: str) -> int:
    idx = 0
    for ch in letter:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


class FakeBook:
    """Spreadsheet 와 같은 표면을 가진 격자 하나. 쓰기는 기록만 한다."""

    def __init__(self):
        self.values: dict[tuple[int, int], object] = {}
        self.formulas: dict[tuple[int, int], str] = {}
        self.writes: list[tuple[str, list[list]]] = []

    def put(self, a1: str, value, formula: str | None = None):
        c, r, _, _ = parse_a1(a1)
        self.values[(c, r)] = value
        if formula:
            self.formulas[(c, r)] = formula

    def _grab(self, a1: str, source: dict):
        c0, r0, c1, r1 = parse_a1(a1)
        rows = []
        for r in range(r0, r1 + 1):
            row = [source.get((c, r), "") for c in range(c0, c1 + 1)]
            while row and row[-1] == "":
                row.pop()
            rows.append(row)
        while rows and not rows[-1]:
            rows.pop()
        return rows

    def read(self, gid, a1):
        return self._grab(a1, self.values)

    def read_formatted(self, gid, a1):
        grid = {k: ("" if v == "" else str(v)) for k, v in self.values.items()}
        return self._grab(a1, grid)

    def read_formulas(self, gid, a1):
        merged = dict(self.values)
        merged.update(self.formulas)
        return self._grab(a1, merged)

    def write(self, gid, a1, values):
        self.writes.append((a1, values))
        c0, r0, _, _ = parse_a1(a1)
        for dr, row in enumerate(values):
            for dc, v in enumerate(row):
                self.values[(c0 + dc, r0 + dr)] = v
        return sum(len(r) for r in values)


def make_daily(header_row=157, slots=6, day="8/28"):
    """오늘 블록이 준비만 되고 아직 비어 있는 Daily 시트."""
    book = FakeBook()
    book.put(f"A{header_row}", day)
    for i in range(slots):
        c1 = tw.col_letter(1 + i * 3)
        c2 = tw.col_letter(2 + i * 3)
        c3 = tw.col_letter(3 + i * 3)
        book.put(f"{c1}{header_row}", "종목명")
        book.put(f"{c2}{header_row}", "상승률")
        book.put(f"{c3}{header_row}", "거래대금(백만)")
        # 대표행: 종목명 칸은 비어 있고 나머지 두 칸에 수식이 미리 들어있다.
        book.put(f"{c2}{header_row + 1}", 0.0, f"=AVERAGEA({c2}{header_row + 2}:{c2}{header_row + 8})")
        book.put(f"{c3}{header_row + 1}", 0, f"=SUM({c3}{header_row + 2}:{c3}{header_row + 8})")
    return book


def group(keyword, names_rates_amounts):
    return {
        "keyword": keyword,
        "top5": [{"name": n, "rate": r, "amount": a, "code": "000000", "cap": 5000}
                 for n, r, a in names_rates_amounts],
    }


GROUPS = [
    group("2차전지", [("엔켐", 21.91, 308000), ("코이즈", 12.3, 41000),
                       ("케이엔에스", 9.55, 22000), ("에코프로", 7.2, 190000),
                       ("포스코퓨처엠", 5.4, 88000)]),
    group("바이오", [("알테오젠", 15.0, 120000), ("셀트리온", 9.9, 70000),
                      ("삼천당제약", 8.1, 33000), ("펩트론", 6.6, 25000),
                      ("리가켐바이오", 5.2, 18000)]),
]

FRIDAY = datetime(2026, 8, 28, 20, 13, tzinfo=KST)

failures: list[str] = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    got : {got}\n    want: {want}")


def test_daily_ranges():
    book = make_daily()
    tw.write_daily(book, GROUPS, FRIDAY)
    ranges = [w[0] for w in book.writes]
    check("Daily 기록 범위", ranges,
          ["B158:B163", "C159:C163", "D159:D163",
           "E158:E163", "F159:F163", "G159:G163"])

    names = book.writes[0][1]
    check("종목명 열은 키워드 + 5종목", names,
          [["2차전지"], ["엔켐"], ["코이즈"], ["케이엔에스"], ["에코프로"], ["포스코퓨처엠"]])
    check("상승률은 백분율 문자열", book.writes[1][1],
          [["21.91%"], ["12.30%"], ["9.55%"], ["7.20%"], ["5.40%"]])
    check("거래대금은 정수", book.writes[2][1],
          [[308000], [41000], [22000], [190000], [88000]])


def test_daily_never_touches_formula_row():
    """대표행의 상승률/거래대금 칸은 어떤 쓰기에도 포함되면 안 된다."""
    book = make_daily()
    tw.write_daily(book, GROUPS, FRIDAY)
    forbidden = set()
    for i in range(6):
        forbidden.add((2 + i * 3, 158))
        forbidden.add((3 + i * 3, 158))
    touched = set()
    for a1, values in book.writes:
        c0, r0, _, _ = parse_a1(a1)
        for dr, row in enumerate(values):
            for dc, _v in enumerate(row):
                touched.add((c0 + dc, r0 + dr))
    clash = sorted(touched & forbidden)
    check("수식 칸을 건드리지 않음", clash, [])


def test_daily_skips_when_already_filled():
    book = make_daily()
    book.put("B158", "2차전지")
    before = len(book.writes)
    tw.write_daily(book, GROUPS, FRIDAY)
    check("이미 채워졌으면 쓰지 않음", len(book.writes), before)


def test_daily_aborts_without_template_formulas():
    book = make_daily()
    book.formulas.pop((2, 158))
    book.values[(2, 158)] = ""
    try:
        tw.write_daily(book, GROUPS, FRIDAY)
    except SystemExit:
        return
    failures.append("템플릿 수식이 없는데도 중단하지 않음")


def test_daily_aborts_on_missing_day():
    book = make_daily(day="8/27")
    try:
        tw.write_daily(book, GROUPS, FRIDAY)
    except SystemExit:
        return
    failures.append("오늘 날짜 블록이 없는데도 중단하지 않음")


def test_daily_aborts_on_duplicate_day():
    book = make_daily()
    book.put("A200", "8/28")
    try:
        tw.write_daily(book, GROUPS, FRIDAY)
    except SystemExit:
        return
    failures.append("날짜가 두 곳인데도 중단하지 않음")


def test_daily_drops_extra_groups():
    book = make_daily(slots=2)
    tw.write_daily(book, GROUPS + [group("비철금속", [("풍산", 6.0, 30000)])], FRIDAY)
    check("슬롯 2개면 2그룹만", len(book.writes), 6)


MARKET = {
    "KOSPI": {"index": 6912.37, "amount_trillion": 22.9, "foreign_eok": 3120},
    "KOSDAQ": {"index": 827.44, "amount_trillion": 9.1, "foreign_eok": -450},
}


def test_monthly_ranges():
    book = FakeBook()
    book.put("N10", 28)          # 금요일 열 묶음 N/O/P
    totals = [{"keyword": "2차전지", "rate": 0.1123, "amount": 649000},
              {"keyword": "바이오", "rate": 0.0896, "amount": 266000}]
    tw.write_monthly(book, totals, MARKET, FRIDAY)
    check("Monthly 기록 범위", [w[0] for w in book.writes],
          ["O10:P10", "O11:P11", "O12:P12", "N14:P15"])
    check("지수 행", book.writes[0][1], [["코스피 6912", "코스닥 827"]])
    check("거래대금 행", book.writes[1][1], [[22.9, 9.1]])
    check("외인 행", book.writes[2][1], [[3120, -450]])
    check("키워드 행", book.writes[3][1],
          [["2차전지", 0.1123, 649000], ["바이오", 0.0896, 266000]])


def test_monthly_leaves_label_row_alone():
    """day_row+3 은 '평균상승률/거래대금합' 라벨 행이므로 비어 있어야 한다."""
    book = FakeBook()
    book.put("N10", 28)
    tw.write_monthly(book, [{"keyword": "2차전지", "rate": 0.1, "amount": 1}], MARKET, FRIDAY)
    rows = {parse_a1(a1)[1] for a1, _ in book.writes}
    check("라벨 행(13)을 건드리지 않음", 13 in rows, False)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001 - 테스트 러너가 통째로 죽으면 안 된다
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
