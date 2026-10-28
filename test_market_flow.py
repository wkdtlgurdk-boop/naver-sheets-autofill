#!/usr/bin/env python3
"""market_flow 한 문장 시험. 네트워크 없이 돈다.

    python test_market_flow.py
"""
import sys

import market_flow as mf

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def test_sentence():
    check("엇갈림", mf.sentence("코스피", 5826, -2824),
          "오늘 코스피는 외인은 순매수, 기관은 순매도 했다개 (외인 +5,826억 · 기관 -2,824억)")
    check("둘 다 같은 쪽", mf.sentence("코스닥", -257, -1507),
          "오늘 코스닥은 외인·기관 모두 순매도 했다개 (외인 -257억 · 기관 -1,507억)")
    check("조 단위", mf.won(-12345), "-1.2조")


def test_lines_and_skip_holidays():
    rows = [{"date": "20261027", "foreignValue": 300, "institutionalValue": -300}]
    check("시장마다 한 줄", mf.lines({"KOSPI": rows}),
          ["오늘 코스피는 외인은 순매수, 기관은 순매도 했다개 (외인 +300억 · 기관 -300억)"])
    check("자료 없으면 빈 줄", mf.lines({}), [])

    def fake(market, day):
        if day in ("20261024", "20261025"):     # 주말은 부르지도 않는다
            raise AssertionError(day)
        if day == "20261023":
            return None                          # 휴장(0) 처리
        return {"date": day, "foreignValue": 1, "institutionalValue": 1}
    h = mf.history("KOSPI", "20261027", n=2, fetch=fake)
    check("휴장·주말 건너뜀", [r["date"] for r in h], ["20261026", "20261027"])


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
