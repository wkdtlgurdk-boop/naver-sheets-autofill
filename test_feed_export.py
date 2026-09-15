#!/usr/bin/env python3
"""시트 내용을 봇으로 내보내는 길의 시험. 네트워크도 시트도 타지 않는다.

    python test_feed_export.py

여기서 못 박는 것은 세 가지다.

  1) 시트에는 종목코드가 없다 -- 이름으로 코드를 붙이고, 못 붙인 것을 숨기지
     않는다.
  2) 수급 창의 주인은 시트다 -- 우리 파일이 아니라 시트의 날짜 줄이 어느 날이
     남는지를 정한다.
  3) 거래대금 단위를 바꾸지 않는다 -- 백만원으로 들어와 백만원으로 나간다.
"""
from __future__ import annotations

import unittest

import feed_export
import supply_demand


def group(keyword, *stocks):
    """(이름, 코드, 등락률, 거래대금) 들을 시트 블록 한 슬롯 모양으로."""
    rows = [{"name": n, "code": c, "rate": r, "amount": a} for n, c, r, a in stocks]
    return {"keyword": keyword, "total_qualifying": len(rows),
            "top5": rows[:5], "stocks": rows}


def supply_day(label, date, *pairs):
    return {"date": date, "label": label,
            "stocks": [{"code": c, "name": n, "amount": a} for c, n, a in pairs]}


class CodeMap(unittest.TestCase):
    def test_스캔의_이름과_코드를_모은다(self):
        raw = [{"qualifying": [{"name": "모두투어", "code": "080160"}]}]
        groups = [group("여행", ("노랑풍선", "104620", 3.2, 11600))]
        self.assertEqual(feed_export.codes_from_scan(raw, groups),
                         {"모두투어": "080160", "노랑풍선": "104620"})

    def test_코드가_빈_항목은_지도에_넣지_않는다(self):
        # 시트에서 복원한 final_groups 는 코드가 "" 다. 그것을 지도에 넣으면
        # 다음 날 멀쩡한 코드를 빈 값으로 덮는다.
        groups = [group("여행", ("모두투어", "", 3.7, 10300))]
        self.assertEqual(feed_export.codes_from_scan([], groups), {})

    def test_여섯_자리로_채운다(self):
        raw = [{"qualifying": [{"name": "동양", "code": "1520"}]}]
        self.assertEqual(feed_export.codes_from_scan(raw, [])["동양"], "001520")


class ThemesFeed(unittest.TestCase):
    def setUp(self):
        self.groups = [group("여행", ("모두투어", "", 3.69, 10300),
                                     ("노랑풍선", "", 3.17, 11600))]

    def test_이름으로_코드를_붙인다(self):
        feed = feed_export.themes_feed(
            self.groups, {"모두투어": "080160", "노랑풍선": "104620"},
            "20260914", "9/14", "2026-09-14T16:11:00+09:00")
        codes = [s["code"] for s in feed["groups"][0]["stocks"]]
        self.assertEqual(codes, ["080160", "104620"])
        self.assertEqual(feed["missing_codes"], [])

    def test_코드를_못_찾으면_이름을_남긴다(self):
        # 사람이 시트에 손으로 적어 넣은 종목이 이렇게 된다. 조용히 빼면
        # 받는 쪽에서 '시트에 없었다'와 구별이 안 된다.
        feed = feed_export.themes_feed(self.groups, {"모두투어": "080160"},
                                       "20260914", "9/14", "t")
        self.assertEqual(feed["missing_codes"], ["노랑풍선"])
        self.assertEqual(feed["groups"][0]["stocks"][1]["code"], "")

    def test_거래대금_단위를_바꾸지_않는다(self):
        feed = feed_export.themes_feed(self.groups, {}, "20260914", "9/14", "t")
        self.assertEqual(feed["groups"][0]["stocks"][0]["amount"], 10300)

    def test_종목이_하나도_없는_그룹은_빠진다(self):
        feed = feed_export.themes_feed([group("빈칸")], {}, "20260914", "9/14", "t")
        self.assertEqual(feed["groups"], [])

    def test_블록이_다섯_줄보다_길면_전부_내보낸다(self):
        six = group("우주항공", *[(f"종목{i}", f"00000{i}", 1.0, 100)
                                  for i in range(1, 7)])
        feed = feed_export.themes_feed([six], {}, "20260914", "9/14", "t")
        self.assertEqual(len(feed["groups"][0]["stocks"]), 6)


class SupplyWindow(unittest.TestCase):
    def test_시트의_날짜_줄이_창의_주인이다(self):
        previous = {"days": [supply_day("9/8", "20260908", ("A", "가", 10)),
                             supply_day("9/10", "20260910", ("B", "나", 20))]}
        today = supply_day("9/14", "20260914", ("C", "다", 30))
        # 시트가 9/8 을 밀어냈다. 우리 파일에 남아 있어도 따라 버린다.
        days = feed_export.merge_supply_window(previous, today, ["9/10", "9/14"])
        self.assertEqual([d["label"] for d in days], ["9/10", "9/14"])

    def test_시트_순서를_따른다(self):
        previous = {"days": [supply_day("9/14", "20260914", ("C", "다", 30)),
                             supply_day("9/10", "20260910", ("B", "나", 20))]}
        days = feed_export.merge_supply_window(previous, None, ["9/10", "9/14"])
        self.assertEqual([d["label"] for d in days], ["9/10", "9/14"])

    def test_재실행이면_오늘_것이_이긴다(self):
        previous = {"days": [supply_day("9/14", "20260914", ("C", "옛것", 1))]}
        today = supply_day("9/14", "20260914", ("C", "새것", 2))
        days = feed_export.merge_supply_window(previous, today, ["9/14"])
        self.assertEqual(days[0]["stocks"][0]["name"], "새것")

    def test_시트에_아직_없는_날은_넣지_않는다(self):
        previous = {"days": [supply_day("9/10", "20260910", ("B", "나", 20))]}
        days = feed_export.merge_supply_window(previous, None, ["9/11", "9/12"])
        self.assertEqual(days, [])

    def test_이전_피드가_없어도_오늘_하루는_나간다(self):
        today = dict(supply_day("9/14", "20260914", ("C", "다", 30)),
                     window_labels=["9/14"])
        feed = feed_export.supply_feed(None, today, "t")
        self.assertEqual(len(feed["days"]), 1)
        self.assertEqual(feed["date"], "20260914")

    def test_날짜_줄을_못_읽었으면_오늘만_남긴다(self):
        today = supply_day("9/14", "20260914", ("C", "다", 30))
        feed = feed_export.supply_feed(None, today, "t")
        self.assertEqual(feed["window"], ["9/14"])
        self.assertEqual(len(feed["days"]), 1)


class SupplySheetBackfill(unittest.TestCase):
    """시트 지난 칸 되살리기. 시트에는 코드가 없어 이름으로 맞춘다."""

    LABELS = ["9/9", "9/10", "9/14", "9/15", ""]
    DATES = ["20260909", "20260910", "", "20260915", ""]
    MAP = {"삼성전자": "005930", "SK하이닉스": "000660"}

    def block(self, *slots):
        """슬롯마다 (이름, 금액) 목록을 받아 20행 x 10열 격자로."""
        grid = [[""] * 10 for _ in range(20)]
        for i, rows in enumerate(slots):
            for r, (name, amount) in enumerate(rows):
                grid[r][i * 2], grid[r][i * 2 + 1] = name, amount
        return grid

    def test_이름으로_코드를_맞춰_날을_만든다(self):
        blocks = [self.block([("삼성전자", 1000)], [("SK하이닉스", 2000)])]
        days, missing = feed_export.supply_days_from_sheet(
            self.LABELS, self.DATES, blocks, self.MAP)
        self.assertEqual([d["label"] for d in days], ["9/9", "9/10"])
        self.assertEqual(days[0]["stocks"][0], {"code": "005930",
                                                "name": "삼성전자", "amount": 1000})
        self.assertEqual(days[0]["date"], "20260909")
        self.assertEqual(missing, [])

    def test_코드를_못_찾은_이름은_빠지고_기록된다(self):
        # 대개 ETF 다. 없는 종목을 지어내는 것보다 빠지는 쪽이 안전하다.
        blocks = [self.block([("삼성전자", 1000), ("KODEX 200", 900)])]
        days, missing = feed_export.supply_days_from_sheet(
            self.LABELS, self.DATES, blocks, self.MAP)
        self.assertEqual(len(days[0]["stocks"]), 1)
        self.assertEqual(missing, ["KODEX 200"])

    def test_네_블록의_같은_종목은_더한다(self):
        one = self.block([("삼성전자", 1000)])
        two = self.block([("삼성전자", 500)])
        days, _ = feed_export.supply_days_from_sheet(
            self.LABELS, self.DATES, [one, two], self.MAP)
        self.assertEqual(days[0]["stocks"][0]["amount"], 1500)

    def test_빈_칸은_날이_되지_않는다(self):
        # 9/14 슬롯(세 번째)에 아무것도 없다.
        blocks = [self.block([("삼성전자", 1000)], [("삼성전자", 1000)], [])]
        days, _ = feed_export.supply_days_from_sheet(
            self.LABELS, self.DATES, blocks, self.MAP)
        self.assertNotIn("9/14", [d["label"] for d in days])

    def test_이미_있는_날은_건드리지_않는다(self):
        # 순매수 API 가 준 날이 더 정확하다 -- 코드가 응답에 직접 들어 있다.
        previous = {"days": [{"label": "9/15", "date": "20260915",
                              "stocks": [{"code": "005930", "name": "API 것",
                                          "amount": 9}]}]}
        sheet = [{"label": "9/15", "date": "20260915", "source": "sheet-backfill",
                  "stocks": [{"code": "005930", "name": "시트 것", "amount": 1}]},
                 {"label": "9/9", "date": "20260909", "source": "sheet-backfill",
                  "stocks": [{"code": "000660", "name": "하이닉스", "amount": 2}]}]
        feed, added = feed_export.merge_backfill(previous, sheet, ["9/9", "9/15"], "t")
        self.assertEqual(added, ["9/9"])
        by = {d["label"]: d for d in feed["days"]}
        self.assertEqual(by["9/15"]["stocks"][0]["name"], "API 것")

    def test_시트_순서대로_정렬한다(self):
        sheet = [{"label": lb, "date": "", "stocks": [{"code": "005930",
                                                       "name": "가", "amount": 1}]}
                 for lb in ("9/15", "9/9", "9/10")]
        feed, _ = feed_export.merge_backfill(None, sheet, ["9/9", "9/10", "9/15"], "t")
        self.assertEqual([d["label"] for d in feed["days"]], ["9/9", "9/10", "9/15"])


class SheetDate(unittest.TestCase):
    def test_구글_시리얼을_날짜로(self):
        # 2026-09-15 = 1899-12-30 기준 46280일
        self.assertEqual(feed_export.sheet_day(46280), "20260915")

    def test_문자열도_받는다(self):
        self.assertEqual(feed_export.sheet_day("2026-09-15"), "20260915")
        self.assertEqual(feed_export.sheet_day("20260915"), "20260915")

    def test_못_읽으면_빈_값(self):
        self.assertEqual(feed_export.sheet_day(""), "")
        self.assertEqual(feed_export.sheet_day(None), "")
        self.assertEqual(feed_export.sheet_day("9/15"), "")

    def test_여덟_자리_문자열을_시리얼로_읽지_않는다(self):
        # '20260915' 를 숫자로 먼저 보면 2천만 일이 지난 날짜가 되어 죽는다.
        self.assertEqual(feed_export.sheet_day("20260915"), "20260915")


class SupplyExport(unittest.TestCase):
    @staticmethod
    def rows(*pairs):
        return [{"itemname": n, "itemcode": c, "accTradeAmount": a}
                for n, c, a in pairs]

    def test_외인과_기관이_같은_종목을_사면_더한다(self):
        raw = {("외인", "KOSPI"): self.rows(("삼성전자", "005930", 3_000_000_000)),
               ("기관", "KOSPI"): self.rows(("삼성전자", "005930", 2_000_000_000))}
        merged = supply_demand.merge_blocks(raw)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["amount"], 5000)          # 백만원
        self.assertEqual(sorted(merged[0]["blocks"]),
                         ["기관/KOSPI", "외인/KOSPI"])

    def test_순매수액_큰_순서로_나온다(self):
        raw = {("외인", "KOSPI"): self.rows(("작은놈", "000001", 1_000_000_000),
                                            ("큰놈", "000002", 9_000_000_000))}
        self.assertEqual([e["name"] for e in supply_demand.merge_blocks(raw)],
                         ["큰놈", "작은놈"])

    def test_코드가_없는_행은_버린다(self):
        raw = {("외인", "KOSPI"): [{"itemname": "이름만", "accTradeAmount": 1}]}
        self.assertEqual(supply_demand.merge_blocks(raw), [])

    def test_네_블록이_같은_날짜_줄이면_그대로_쓴다(self):
        w = {f"{t}/{m}": ["9/10", "9/11", "9/12", "9/14", ""]
             for t in ("외인", "기관") for m in ("KOSPI", "KOSDAQ")}
        self.assertEqual(supply_demand.consensus_window(w),
                         ["9/10", "9/11", "9/12", "9/14"])

    def test_날짜_줄이_갈리면_다수를_쓴다(self):
        w = {"외인/KOSPI": ["9/14"], "외인/KOSDAQ": ["9/14"],
             "기관/KOSPI": ["9/14"], "기관/KOSDAQ": ["9/11"]}
        self.assertEqual(supply_demand.consensus_window(w), ["9/14"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
