#!/usr/bin/env python3
"""대상일 계산 시험. 네트워크도 시계도 타지 않는다.

    python test_as_of.py

2026-09-18 에 예약 실행이 3시간 34분 늦어 23:54 에 시작하고 00:01 에 끝났다.
앞 단계는 9/18, 뒤 단계는 9/19 를 봐서 시트와 카드의 날짜가 갈라졌다. 그
경계들을 여기서 못 박는다.
"""
from __future__ import annotations

import os
import unittest
from datetime import datetime

import as_of as m


def at(text: str) -> datetime:
    """'2026-09-18 23:54' -> KST datetime."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=m.KST)


def day_of(text: str) -> str:
    return f"{m.trading_day(at(text)):%Y-%m-%d}"


class TradingDay(unittest.TestCase):
    def test_마감_뒤_평일은_오늘(self):
        # 2026-09-18 은 금요일. 20:20 정시 실행.
        self.assertEqual(day_of("2026-09-18 20:20"), "2026-09-18")

    def test_마감_정각은_오늘로_본다(self):
        self.assertEqual(day_of("2026-09-18 20:00"), "2026-09-18")

    def test_마감_직전은_어제(self):
        # 19:59 에는 오늘 종가가 아직 없다.
        self.assertEqual(day_of("2026-09-18 19:59"), "2026-09-17")

    def test_금요일_실행이_자정을_넘겨도_금요일(self):
        # 이게 9/18 사고다. 옛 규칙은 `hour < 20 and weekday() < 5` 였는데
        # 토요일 00:01 은 hour 조건만 맞고 weekday 조건이 틀려 물러나지
        # 않았다 -- 그래서 토요일(9/19)이 대상일이 됐다.
        self.assertEqual(day_of("2026-09-19 00:01"), "2026-09-18")

    def test_자정_전후가_같은_날을_가리킨다(self):
        # 갈라짐의 본질. 한 실행이 자정을 걸쳐도 답이 하나여야 한다.
        self.assertEqual(day_of("2026-09-18 23:54"), day_of("2026-09-19 00:01"))

    def test_토요일_낮도_금요일(self):
        self.assertEqual(day_of("2026-09-19 14:00"), "2026-09-18")

    def test_일요일도_금요일(self):
        self.assertEqual(day_of("2026-09-20 14:00"), "2026-09-18")

    def test_월요일_장중은_금요일(self):
        # 주말을 건너뛰어 물러난다. 일요일을 거래일이라 부르면 안 된다.
        self.assertEqual(day_of("2026-09-21 10:00"), "2026-09-18")

    def test_월요일_마감_뒤는_월요일(self):
        self.assertEqual(day_of("2026-09-21 20:30"), "2026-09-21")

    def test_시각을_떼고_돌려준다(self):
        # 날짜지 순간이 아니다. 시각이 남아 있으면 단계마다 다른 값이 되고,
        # 그게 처음부터 이 사고의 씨앗이었다.
        got = m.trading_day(at("2026-09-18 23:54"))
        self.assertEqual((got.hour, got.minute, got.second), (0, 0, 0))


class Environment(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in (m.AS_OF_ENV, m.PIN_ENV)}

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def test_사람이_지목하면_되살리는_실행이다(self):
        os.environ[m.AS_OF_ENV] = "2026-09-11"
        day, overridden = m.as_of()
        self.assertEqual(f"{day:%Y-%m-%d}", "2026-09-11")
        self.assertTrue(overridden)

    def test_박아둔_날짜는_되살리는_실행이_아니다(self):
        # 여기서 True 를 주면 정규 실행이 매번 '되살리기'로 오인되어
        # 스레드 게시가 조용히 꺼진다.
        os.environ[m.PIN_ENV] = "2026-09-18"
        day, overridden = m.as_of()
        self.assertEqual(f"{day:%Y-%m-%d}", "2026-09-18")
        self.assertFalse(overridden)

    def test_사람의_지목이_박아둔_값을_이긴다(self):
        os.environ[m.PIN_ENV] = "2026-09-18"
        os.environ[m.AS_OF_ENV] = "2026-09-11"
        day, overridden = m.as_of()
        self.assertEqual(f"{day:%Y-%m-%d}", "2026-09-11")
        self.assertTrue(overridden)

    def test_빈_값은_없는_것으로_본다(self):
        # 워크플로가 inputs.as_of 를 그대로 넘기므로 평소에는 빈 문자열이 온다.
        os.environ[m.AS_OF_ENV] = ""
        os.environ[m.PIN_ENV] = "2026-09-18"
        self.assertEqual(f"{m.as_of()[0]:%Y-%m-%d}", "2026-09-18")

    def test_형식이_틀리면_멈춘다(self):
        os.environ[m.AS_OF_ENV] = "9/18"
        with self.assertRaises(SystemExit):
            m.as_of()


if __name__ == "__main__":
    unittest.main(verbosity=2)
