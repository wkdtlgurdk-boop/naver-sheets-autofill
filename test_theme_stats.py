#!/usr/bin/env python3
"""theme_stats 의 날짜·이어짐·수익률 계산 검증. 네트워크도 인증도 없이 돈다.

실행: python test_theme_stats.py
"""
from __future__ import annotations

import sys
import unittest
from datetime import date

import theme_stats as ts


def group(keyword, *names_rates):
    return {"keyword": keyword,
            "stocks": [{"name": n, "rate": r} for n, r in names_rates]}


def bars(*rows):
    return [{"date": d, "open": o, "close": c} for d, o, c in rows]


D = [date(2026, 9, d) for d in (14, 15, 16, 17, 18, 21, 22)]


class TabNames(unittest.TestCase):
    def test_세_가지_이름을_읽는다(self):
        self.assertEqual(ts.tab_year_month("26.9월"), (2026, 9))
        self.assertEqual(ts.tab_year_month("2026- 1월"), (2026, 1))
        self.assertEqual(ts.tab_year_month("2025-6월"), (2025, 6))
        self.assertEqual(ts.tab_year_month("12월"), (2025, 12))
        self.assertEqual(ts.tab_year_month("3월"), (2026, 3))
        self.assertIsNone(ts.tab_year_month("복사해서 쓰시오 지우지마시오"))

    def test_앞뒤_달이_섞인_블록(self):
        self.assertEqual(ts.parse_block_day("8/31", 2026, 9), date(2026, 8, 31))
        self.assertEqual(ts.parse_block_day("12/31", 2026, 1), date(2025, 12, 31))
        self.assertIsNone(ts.parse_block_day("빼빼로데이", 2025, 11))

    def test_이름_꼬리표를_뗀다(self):
        self.assertEqual(ts.clean_name("셀리드\xa0*"), "셀리드")


class Grid(unittest.TestCase):
    def test_블록을_읽고_엉뚱한_달은_버린다(self):
        grid = [[""] * 10 for _ in range(30)]
        grid[3][0], grid[3][1] = "9/22", "종목명"
        grid[4][1] = "원전"
        grid[5][1], grid[5][2] = "A", "10.00%"
        grid[6][1], grid[6][2] = "B", "29.90%"
        grid[15][0], grid[15][1] = "2/11", "종목명"   # 9월 탭의 2월 블록
        grid[16][1] = "딴것"
        grid[17][1], grid[17][2] = "C", "5.00%"
        days = ts.days_from_grid(grid, 2026, 9)
        self.assertEqual(list(days), [date(2026, 9, 22)])
        # 상승률 순으로 다시 세운다 -- 대장주는 맨 앞이다.
        self.assertEqual([s["name"] for s in days[date(2026, 9, 22)][0]["stocks"]], ["B", "A"])


class Streaks(unittest.TestCase):
    def test_이름이_달라도_종목이_겹치면_이어진다(self):
        days = {
            D[0]: [group("반도체 기판·AI PCB", ("x", 9), ("y", 8), ("z", 7))],
            D[1]: [group("반도체 기판·PCB (FC-BGA)", ("y", 9), ("x", 8))],
            D[2]: [group("바이오", ("q", 5))],
        }
        rows = ts.link_streaks(days, D)
        self.assertEqual([r["streak"] for r in rows], [1, 2, 1])
        self.assertEqual([r["continued"] for r in rows], [True, False, None])
        self.assertEqual(rows[0]["lineage"], rows[1]["lineage"])

    def test_시트에_빠진_거래일이_있으면_끊는다(self):
        days = {D[0]: [group("원전", ("a", 9))], D[2]: [group("원전", ("a", 9))]}
        rows = ts.link_streaks(days, D)
        self.assertEqual([r["streak"] for r in rows], [1, 1])
        self.assertEqual(rows[0]["continued"], None)

    def test_주말은_건너뛰고_잇는다(self):
        days = {D[4]: [group("원전", ("a", 9))], D[5]: [group("원전", ("a", 9))]}
        rows = ts.link_streaks(days, D)
        self.assertEqual([r["streak"] for r in rows], [1, 2])


class Outcome(unittest.TestCase):
    def test_다음날_시가_진입(self):
        b = bars((D[0], 100, 100), (D[1], 110, 121), (D[2], 120, 99),
                 (D[3], 100, 132))
        out = ts.outcome(b, D[0])
        self.assertAlmostEqual(out["gap"], 10.0)
        self.assertAlmostEqual(out["r1"], 10.0)
        self.assertAlmostEqual(out["r3"], 20.0)
        self.assertIsNone(out["r5"])

    def test_마지막날은_결과가_없다(self):
        self.assertIsNone(ts.outcome(bars((D[0], 1, 1)), D[0]))


class Build(unittest.TestCase):
    def test_집계와_오늘_테마(self):
        days = {D[0]: [group("원전", ("a", 30), ("b", 10))],
                D[1]: [group("원전", ("a", 12), ("b", 11))]}
        price = {"000001": bars((D[0], 10, 10), (D[1], 11, 12), (D[2], 12, 12)),
                 "000002": bars((D[0], 10, 10), (D[1], 10, 10), (D[2], 10, 11))}
        stats = ts.build(days, price, {"a": "000001", "b": "000002"}, "t")
        s1 = stats["by_streak"]["1일차"]
        self.assertEqual(s1["n"], 1)
        self.assertEqual(s1["continue_pct"], 100.0)
        self.assertAlmostEqual(s1["leader"]["r1"]["mean"], 9.09, places=2)
        self.assertEqual(stats["by_leader_rate"]["a.상한가"]["n"], 1)
        self.assertEqual(stats["current"][0]["streak"], 2)
        self.assertEqual(stats["current"][0]["bucket"], "2일차")
        self.assertIn("테마 지속성", ts.report_md(stats))


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
