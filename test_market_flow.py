#!/usr/bin/env python3
"""market_flow 한 줄 평 시험. 네트워크 없이 돈다.

    python test_market_flow.py
"""
import sys

import market_flow as mf

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def test_buy_sell_and_size():
    usual = [1000, -1000] * 10
    check("평소 규모로 샀다", mf.verdict(usual + [1000]), "샀다 (+1,000억) · 1일 만에 순매수 전환")
    check("많이 팔았다", mf.verdict([500] * 20 + [-1500]), "많이 팔았다 (-1,500억) · 20일 만에 순매도 전환")
    check("적게 팔았다", mf.verdict([-1000] * 20 + [-200])[:12], "적게 팔았다 (-200")
    check("조 단위", mf.won(12345), "1.2조")


def test_streak_and_turn():
    check("3일 연속", mf.verdict([-300, 400, 300, 500, 400]), "샀다 (+400억) · 4일 연속 순매수")
    check("2일 연속은 말 안 함", mf.verdict([-300, -400, 300, 400]), "샀다 (+400억)")
    check("3일 만에 전환", mf.verdict([500, -300, -400, -350, 600]), "샀다 (+600억) · 3일 만에 순매수 전환")
    check("0", mf.verdict([100, 0]), "거의 안 움직였다")


def test_lines_and_skip_holidays():
    rows = [{"date": f"2026102{i}", "foreignValue": v, "institutionalValue": -v} for i, v in enumerate([100, 200, 300])]
    got = mf.lines({"KOSPI": rows})
    check("코스피 두 줄", got, ["코스피 외인이 많이 샀다 (+300억) · 3일 연속 순매수",
                              "코스피 기관이 많이 팔았다 (-300억) · 3일 연속 순매도"])

    def fake(market, day):
        if day in ("20261024", "20261025"):     # 주말은 부르지도 않는다
            raise AssertionError(day)
        if day == "20261023":
            return None                          # 휴장(0) 처리
        return {"date": day, "foreignValue": 1, "institutionalValue": 1}
    h = mf.history("KOSPI", "20261027", n=3, fetch=fake)
    check("휴장·주말 건너뜀", [r["date"] for r in h], ["20261021", "20261022", "20261026", "20261027"][-3:])


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            t()
        except Exception as exc:
            failures.append(f"{t.__name__} 예외: {exc!r}")
    if failures:
        print("\n".join(failures))
        return 1
    print(f"통과 {len(tests)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
