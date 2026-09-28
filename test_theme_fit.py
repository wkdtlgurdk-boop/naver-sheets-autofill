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



def theme(name, members, *stocks):
    return {"theme_name": name, "member_count": members,
            "qualifying": [stock(n, c, r) for n, c, r in stocks]}


class NarrowFirst(unittest.TestCase):
    def test_좁은_테마_종목을_먼저_채운다(self):
        # 2026-09-28 의료AI·의료기기: 바이오 랠리로 더 오른 의료기기 종목이
        # 의료AI 종목을 밀어냈다.
        groups = [{"keyword": "의료AI·의료기기", "themes": [
            theme("의료AI", 25, ("뷰노", "1", 29.98), ("쓰리빌리언", "2", 23.97),
                  ("셀바스헬스케어", "3", 15.57), ("루닛", "4", 14.48), ("딥노이드", "5", 12.4)),
            theme("의료기기", 110, ("한국비엔씨", "6", 29.98), ("레메디", "7", 23.08),
                  ("동국생명과학", "8", 18.21), ("뷰노", "1", 29.98)),
        ]}]
        top = ts.build_output(groups, {})[0]["top5"]
        self.assertEqual([s["name"] for s in top],
                         ["뷰노", "쓰리빌리언", "셀바스헬스케어", "루닛", "딥노이드"])

    def test_좁은_쪽이_모자라면_넓은_쪽으로_채운다(self):
        groups = [{"keyword": "의료AI·의료기기", "themes": [
            theme("의료AI", 25, ("뷰노", "1", 29.98), ("루닛", "4", 14.48)),
            theme("의료기기", 110, ("한국비엔씨", "6", 29.98), ("레메디", "7", 23.08),
                  ("동국생명과학", "8", 18.21), ("메디아나", "9", 8.0)),
        ]}]
        top = ts.build_output(groups, {})[0]["top5"]
        # 좁은 쪽 둘 + 넓은 쪽 상위 셋, 적는 순서는 상승률 순.
        self.assertEqual([s["name"] for s in top],
                         ["뷰노", "한국비엔씨", "레메디", "동국생명과학", "루닛"])

    def test_같은_테마의_갈래는_먼저_채우지_않는다(self):
        groups = [{"keyword": "2차전지", "themes": [
            theme("2차전지(전고체)", 20, ("전고체주", "1", 6.0)),
            theme("2차전지", 128, ("대장주", "2", 15.0), ("둘째", "3", 11.0),
                  ("셋째", "4", 8.0), ("넷째", "5", 7.0), ("다섯째", "6", 6.5)),
        ]}]
        top = ts.build_output(groups, {})[0]["top5"]
        self.assertNotIn("전고체주", [s["name"] for s in top])


if __name__ == "__main__":
    result = unittest.main(verbosity=1, exit=False).result
    sys.exit(0 if result.wasSuccessful() else 1)
