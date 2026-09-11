#!/usr/bin/env python3
"""groups_from_sheet 의 읽기 규칙을 못 박는다. 네트워크를 타지 않는다.

    python test_groups_from_sheet.py

여기서 틀리면 100배 틀린 금액이나 엉뚱한 종목이 카드에 찍혀 공개로 올라간다.
"""
from __future__ import annotations

import unittest

from groups_from_sheet import (block_date, find_header_row, groups_from_grid,
                               parse_amount_eok, parse_rate)


def grid_of(rows: dict[int, dict[int, str]], height: int = 24, width: int = 12):
    """{행: {열: 값}} -> 2차원 격자. 행은 1-based, 열은 0-based."""
    g = [["" for _ in range(width)] for _ in range(height)]
    for r, cols in rows.items():
        for c, v in cols.items():
            g[r - 1][c] = v
    return g


def sample():
    """theme_write 가 쓰는 배치 그대로. 슬롯 둘(1열, 4열)."""
    return grid_of({
        3: {0: "9/10"},                                   # 어제 블록
        5: {0: "9/11", 1: "종목명", 2: "상승률", 3: "거래대금(억)",
            4: "종목명", 5: "상승률", 6: "거래대금(억)"},   # 헤더행
        6: {0: "9/11", 1: "우주/방산", 2: "11.09%", 3: "4,413",
            4: "라면", 5: "8.16%", 6: "2,701"},            # 대표행
        7: {0: "9/11", 1: "쎄트랙아이", 2: "22.00%", 3: "244",
            4: "농심", 5: "19.17%", 6: "1,284"},
        8: {0: "9/11", 1: "RF머트리얼즈", 2: "12.55%", 3: "150"},
        9: {0: "9/11", 1: "퍼스텍", 2: "11.93%", 3: "886"},
        10: {0: "9/11", 1: "엠앤씨솔루션", 2: "7.61%", 3: "86"},
        11: {0: "9/11", 1: "삼양컴텍", 2: "7.07%", 3: "1,112"},
        12: {0: "9/11", 1: "한국항공우주", 2: "5.37%", 3: "1,935"},
    })


class Parsing(unittest.TestCase):
    def test_상승률은_퍼센트_표기를_읽는다(self):
        self.assertEqual(parse_rate("22.00%"), 22.0)
        self.assertEqual(parse_rate("-3.5%"), -3.5)
        self.assertEqual(parse_rate("12.34"), 12.34)   # 숫자만 와도 받는다
        self.assertIsNone(parse_rate(""))
        self.assertIsNone(parse_rate("#DIV/0!"))

    def test_거래대금은_천단위_쉼표를_뗀다(self):
        self.assertEqual(parse_amount_eok("1,689"), 1689)
        self.assertEqual(parse_amount_eok("244"), 244)
        self.assertIsNone(parse_amount_eok(""))


class HeaderRow(unittest.TestCase):
    def test_종목명_헤더가_있는_행만_고른다(self):
        # 날짜 셀이 세로 병합이라 같은 날짜가 여러 행에 보인다. 헤더는 하나뿐.
        self.assertEqual(find_header_row(sample(), "9/11"), 5)

    def test_없는_날짜는_None(self):
        self.assertIsNone(find_header_row(sample(), "9/12"))

    def test_병합_날짜가_헤더행_위에_있어도_찾는다(self):
        # Sheets API 는 병합 범위의 값을 좌상단 한 칸에만 준다. 좌상단이
        # 헤더행보다 위면 헤더행의 A 는 비어 있다 -- 위로 훑어야 찾는다.
        g = sample()
        g[5 - 1][0] = ""          # 헤더행 A 를 비우고
        g[4 - 1][0] = "9/11"      # 한 칸 위에 날짜를 둔다
        self.assertEqual(block_date(g, 5), "9/11")
        self.assertEqual(find_header_row(g, "9/11"), 5)

    def test_아래_블록의_날짜를_끌어오지_않는다(self):
        # 위로만 훑는다. 아래 블록 날짜를 집으면 남의 날을 오늘로 읽는다.
        g = sample()
        g[5 - 1][0] = ""
        g[4 - 1][0] = ""
        g[3 - 1][0] = "9/10"      # 위에 있는 것은 어제뿐
        self.assertIsNone(find_header_row(g, "9/11"))


class Groups(unittest.TestCase):
    def test_슬롯마다_그룹이_하나씩(self):
        gs = groups_from_grid(sample(), "9/11")
        self.assertEqual([g["keyword"] for g in gs], ["우주/방산", "라면"])

    def test_대표행은_종목이_아니다(self):
        # header_row+1 은 키워드와 합계 수식이 있는 줄이라 종목에 섞이면 안 된다.
        gs = groups_from_grid(sample(), "9/11")
        self.assertEqual(gs[0]["top5"][0]["name"], "쎄트랙아이")
        self.assertNotIn("우주/방산", [s["name"] for s in gs[0]["top5"]])

    def test_여섯_종목이어도_개수는_여섯으로_센다(self):
        # 9/11 우주/방산 블록이 실제로 여섯이었다. 다섯에서 자르고 세면
        # 카드 문구가 "6종목 중 상위 5개" 가 아니라 "5종목 중 5개" 가 된다.
        gs = groups_from_grid(sample(), "9/11")
        self.assertEqual(gs[0]["total_qualifying"], 6)
        self.assertEqual(len(gs[0]["top5"]), 5)

    def test_빈_줄에서_블록이_끝난다(self):
        # 아래 블록의 종목을 빨아들이면 안 된다.
        g = sample()
        g[14 - 1][1] = "다음블록종목"
        gs = groups_from_grid(g, "9/11")
        self.assertNotIn("다음블록종목", [s["name"] for s in gs[0]["top5"]])

    def test_거래대금은_억에서_백만원으로_바뀐다(self):
        # cards.eok 가 100 으로 나눠 억을 만든다. 여기서 100 을 곱해야 맞는다.
        gs = groups_from_grid(sample(), "9/11")
        self.assertEqual(gs[0]["top5"][0]["amount"], 244 * 100)
        self.assertEqual(sum(s["amount"] for s in gs[0]["top5"]) // 100,
                         244 + 150 + 886 + 86 + 1112)

    def test_빈_슬롯과_오류셀은_건너뛴다(self):
        g = sample()
        g[6 - 1][4] = "#DIV/0!"      # 라면 자리를 오류로  # noqa
        gs = groups_from_grid(g, "9/11")
        self.assertEqual([x["keyword"] for x in gs], ["우주/방산"])

    def test_날짜가_없으면_빈_목록(self):
        self.assertEqual(groups_from_grid(sample(), "9/12"), [])

    def test_카드가_요구하는_키가_다_있다(self):
        g = groups_from_grid(sample(), "9/11")[0]
        for key in ("keyword", "source_themes", "total_qualifying", "top5"):
            self.assertIn(key, g)
        for key in ("name", "rate", "amount"):
            self.assertIn(key, g["top5"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
