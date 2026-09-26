#!/usr/bin/env python3
"""weekly 의 묶기·주간 수익률·본문 검증. 카드 렌더는 test_cards 쪽 몫이다.

실행: python test_weekly.py
"""
from __future__ import annotations

import sys
import unittest
from datetime import date

import weekly as wk

MON, FRI = date(2026, 9, 21), date(2026, 9, 25)
D = [date(2026, 9, d) for d in (21, 22, 23)]


def group(keyword, *names_rates):
    return {"keyword": keyword, "stocks": [{"name": n, "rate": r} for n, r in names_rates]}


def bars(*rows):
    return [{"date": d, "open": c, "close": c} for d, c in rows]


class Dates(unittest.TestCase):
    def test_주와_이름(self):
        self.assertEqual(wk.week_of(date(2026, 9, 23)), (MON, FRI))
        self.assertEqual(wk.week_label(FRI), "9월 넷째 주")
        self.assertEqual(wk.week_label(date(2026, 10, 2)), "10월 첫째 주")


class Cluster(unittest.TestCase):
    def test_이름이_흔들려도_한_테마(self):
        days = {
            D[0]: [group("반도체 기판·AI PCB", ("x", 9), ("y", 8))],
            D[1]: [group("반도체 기판·PCB", ("x", 5), ("y", 4)), group("원전", ("a", 7))],
            D[2]: [group("반도체 기판·PCB", ("y", 6), ("z", 3))],
        }
        cs = wk.cluster_week(days)
        self.assertEqual(len(cs), 2)
        self.assertEqual(cs[0]["keyword"], "반도체 기판·PCB")
        self.assertEqual(len(cs[0]["days"]), 3)
        self.assertEqual(cs[0]["stocks"]["x"], 9)


class Returns(unittest.TestCase):
    def test_지난주_종가에서_이번주_마지막_종가(self):
        b = bars((date(2026, 9, 18), 100), (D[0], 105), (D[2], 120))
        self.assertAlmostEqual(wk.week_return(b, MON, FRI), 20.0)
        self.assertIsNone(wk.week_return(bars((D[0], 1)), MON, FRI))

    def test_요약(self):
        days = {D[0]: [group("원전", ("a", 9), ("b", 5))],
                D[1]: [group("원전", ("a", 3), ("b", 2)), group("바이오", ("c", 8))]}
        price = {"1": bars((date(2026, 9, 18), 100), (D[2], 130)),
                 "2": bars((date(2026, 9, 18), 100), (D[2], 110)),
                 "3": bars((date(2026, 9, 18), 100), (D[2], 90))}
        s = wk.summarize_week(days, price, {"a": "1", "b": "2", "c": "3"}, MON, FRI)
        self.assertEqual([t["keyword"] for t in s["themes"]], ["원전", "바이오"])
        self.assertEqual(s["themes"][0]["days"], 2)
        self.assertAlmostEqual(s["themes"][0]["week_return"], 20.0)
        self.assertEqual([x["name"] for x in s["stocks"]], ["a", "b", "c"])
        self.assertEqual(s["stocks"][0]["days"], 2)

        text = wk.caption(s, FRI)
        first = text.split("\n", 1)[0]
        # 하루 글 표식('2026년 9월 25일')과 겹치면 금요일 하루 실행이 게시를 건너뛴다.
        self.assertNotIn("9월 25일", first)
        self.assertIn("1. 원전 2일 +20.00%", text)


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
