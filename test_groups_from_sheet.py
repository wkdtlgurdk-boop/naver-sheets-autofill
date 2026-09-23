#!/usr/bin/env python3
"""groups_from_sheet 의 읽기 규칙을 못 박는다. 네트워크를 타지 않는다.

    python test_groups_from_sheet.py

여기서 틀리면 100배 틀린 금액이나 엉뚱한 종목이 카드에 찍혀 공개로 올라간다.
"""
from __future__ import annotations

import pathlib
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


def duplicated_block():
    """9/22 블록의 모양. 같은 그룹이 이름만 달리해 세 칸을 먹었다.

    실물 그대로다 -- 종목 다섯과 상승률까지 세 칸이 모두 같고, 이름만
    괄호와 공백이 다르다. 카드가 같은 테마를 세 장 냈던 자리다.
    """
    pcb = [("코리아써키트", "26.96%", "1,953"), ("티엘비", "17.06%", "1,102"),
           ("인터플렉스", "14.70%", "169"), ("네오티스", "14.46%", "124"),
           ("티에프이", "10.65%", "350")]
    rows = {
        5: {0: "9/22", 1: "종목명", 2: "상승률", 3: "거래대금(억)",
            4: "종목명", 5: "상승률", 6: "거래대금(억)",
            7: "종목명", 8: "상승률", 9: "거래대금(억)"},
        6: {1: "온디바이스 AI", 4: "반도체 기판·PCB(FC-BGA/SOCAMM)",
            7: "반도체 기판·PCB"},
    }
    ondevice = [("노타", "29.97%", "526"), ("심텍", "13.93%", "2,806")]
    for i, (name, rate, amount) in enumerate(ondevice, start=1):
        rows.setdefault(6 + i, {}).update({1: name, 2: rate, 3: amount})
    for i, (name, rate, amount) in enumerate(pcb, start=1):
        rows.setdefault(6 + i, {}).update({4: name, 5: rate, 6: amount,
                                           7: name, 8: rate, 9: amount})
    return grid_of(rows, height=20, width=12)


class 중복슬롯(unittest.TestCase):
    """이름이 흔들려 한 그룹이 여러 칸에 앉은 블록.

    theme_write 는 키워드 문자열이 정확히 같을 때만 칸을 재사용한다. 그룹
    이름은 모델이 붙이는 자유 서술이라 하루에 여러 번 돌면 철자가 흔들리고,
    그때마다 빈 칸을 하나씩 먹는다. 읽는 쪽에서 막는다.
    """

    def test_종목이_같은_칸은_하나만_남는다(self):
        groups = groups_from_grid(duplicated_block(), "9/22")
        self.assertEqual([g["keyword"] for g in groups],
                         ["온디바이스 AI", "반도체 기판·PCB(FC-BGA/SOCAMM)"])

    def test_먼저_나온_칸이_남는다(self):
        groups = groups_from_grid(duplicated_block(), "9/22")
        pcb = groups[1]
        self.assertEqual([s["name"] for s in pcb["top5"]][0], "코리아써키트")
        self.assertEqual(pcb["total_qualifying"], 5)

    def test_종목이_다르면_건드리지_않는다(self):
        # 겹치기만 하는 칸은 실제로 다른 테마일 수 있다. 완전히 같을 때만 접는다.
        grid = duplicated_block()
        grid[10][7] = "다른종목"      # 11행(세 번째 칸 마지막 줄)만 바꾼다
        groups = groups_from_grid(grid, "9/22")
        self.assertEqual(len(groups), 3)


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

    def test_거래대금은_변환하지_않는다(self):
        """시트 칸과 final_groups 의 단위는 이미 같다 (둘 다 백만원).

        theme_write.write_daily 가 int(s["amount"]) 를 그대로 시트에 쓴다.
        시트를 억으로 보고 100 을 곱했더니 9/11 카드에 596,940억(=59.7조)이
        찍혀 공개 리포에 올라갔다. 같은 날 스캔 경로로 만든 카드는 5,969억
        이었다 -- 정확히 100 배였고, 이 시험이 그 틀린 가정을 못 박고 있었다.
        """
        gs = groups_from_grid(sample(), "9/11")
        self.assertEqual(gs[0]["top5"][0]["amount"], 244)
        # cards.eok 가 100 으로 나눠 억을 만든다. 244 백만원 = 2억.
        import cards
        self.assertEqual(cards.eok(gs[0]["top5"][0]["amount"]), round(244 / 100))

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


class AsOfDrivesWhichBlock(unittest.TestCase):
    """지난 날짜 재실행은 그 날 블록을 읽어야 한다.

    9/14 실행이 여기서 막혔다. 스캔이 0개를 받아 시트를 읽어야 했는데, 읽더라도
    오늘(9/14) 블록을 찾았을 것이다 -- 아직 비어 있는 칸이다. 되살리려는 날짜와
    읽는 날짜가 같아야 한다.
    """

    def wanted(self, as_of_value, date_arg=None):
        import importlib
        import os

        import as_of as mod
        old = os.environ.get("THEME_AS_OF")
        if as_of_value is None:
            os.environ.pop("THEME_AS_OF", None)
        else:
            os.environ["THEME_AS_OF"] = as_of_value
        try:
            importlib.reload(mod)
            now, overridden = mod.as_of()
            return date_arg or f"{now.month}/{now.day}", overridden
        finally:
            if old is None:
                os.environ.pop("THEME_AS_OF", None)
            else:
                os.environ["THEME_AS_OF"] = old
            importlib.reload(mod)

    def test_as_of_picks_that_days_block(self):
        self.assertEqual(self.wanted("2026-09-11"), ("9/11", True))

    def test_explicit_date_still_wins(self):
        wanted, _ = self.wanted("2026-09-11", date_arg="9/8")
        self.assertEqual(wanted, "9/8")

    def test_the_script_reads_the_shared_as_of(self):
        src = pathlib.Path(__file__).with_name("groups_from_sheet.py").read_text(
            encoding="utf-8")
        self.assertIn("from as_of import as_of", src)
        self.assertNotIn("datetime.now(KST)", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
