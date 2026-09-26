#!/usr/bin/env python3
"""theme_fit 의 오배정 판정과 build_output 연결 검증.

실행: python test_theme_fit.py
"""
from __future__ import annotations

import sys
import unittest

import theme_fit as tf
import theme_scan as ts

AFF = {
    "삼성전자": {"반도체": 25, "삼전닉스": 3, "반도체 생산": 2, "자율주행차": 1,
              "SOCAMM": 1, "IDM": 1, "CXL": 1, "반도체 IDM": 1},
    "한전기술": {"원자력": 14, "원전": 9, "전력": 1, "밸류업": 1, "SMR": 1},
    "삼현": {"로봇": 12, "자율주행": 2, "자율주행차": 1, "자동차 부품": 1},
    "신규주": {"자율주행차": 1},
}


class Misfit(unittest.TestCase):
    def test_반도체로_오른_삼성전자는_자율주행차가_아니다(self):
        self.assertIsNotNone(tf.misfit("삼성전자", ["자율주행차"], AFF))
        self.assertIsNone(tf.misfit("삼성전자", ["반도체 소재·장비·HBM"], AFF))

    def test_이웃_산업은_오배정이_아니다(self):
        # 원자력 종목이 '전력설비' 칸에 앉는 것은 같은 흐름이다.
        self.assertIsNone(tf.misfit("한전기술", ["전력설비"], AFF))
        # 낱말이 서로를 품으면 같은 계열 ('자율주행' ⊂ '자율주행차').
        self.assertIsNone(tf.misfit("삼현", ["자율주행차", "로봇"], AFF))

    def test_기록이_적으면_판단하지_않는다(self):
        self.assertIsNone(tf.misfit("신규주", ["반도체"], AFF))
        self.assertIsNone(tf.misfit("처음보는주", ["반도체"], AFF))

    def test_기록을_만든다(self):
        days = {"d1": [{"keyword": "반도체", "stocks": [{"name": "A", "rate": 5}]}],
                "d2": [{"keyword": "반도체", "stocks": [{"name": "A", "rate": 5}]},
                       {"keyword": "로봇", "stocks": [{"name": "A", "rate": 5}]}]}
        self.assertEqual(tf.build_affinity(days), {"A": {"반도체": 2, "로봇": 1}})


def stock(name, code, rate):
    return {"name": name, "code": code, "rate": rate, "cap": 10000, "amount": 50000}


class BuildOutput(unittest.TestCase):
    def test_오배정_종목은_제_계열_그룹으로_간다(self):
        groups = [
            {"keyword": "자율주행차", "themes": [{"theme_name": "자율주행차", "qualifying": [
                stock("삼성전자", "005930", 9.0), stock("삼현", "437730", 8.0),
                stock("드림텍", "192650", 7.0), stock("모트렉스", "118990", 6.0)]}]},
            {"keyword": "반도체", "themes": [{"theme_name": "반도체 대표주", "qualifying": [
                stock("SK하이닉스", "000660", 5.0), stock("삼성전자", "005930", 9.0),
                stock("DB하이텍", "000990", 4.0)]}]},
        ]
        out = {g["keyword"]: [s["name"] for s in g["top5"]]
               for g in ts.build_output(groups, AFF)}
        self.assertNotIn("삼성전자", out["자율주행차"])
        self.assertIn("삼성전자", out["반도체"])


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
