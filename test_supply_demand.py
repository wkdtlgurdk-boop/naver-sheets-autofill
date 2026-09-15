#!/usr/bin/env python3
"""손으로 채우는 경로만 시험한다.

API 경로는 네이버가 살아 있어야 돌아가므로 시험 대상이 아니다. --backfill 은
사람이 HTS 를 보고 옮겨 적는 통로라서 오타가 곧 시트의 틀린 숫자가 된다.
검증이 실제로 잡는지를 여기서 못박아 둔다.
"""
from __future__ import annotations

import supply_demand as sd

NAMES = [f"종목{i}" for i in range(1, 21)]


def block(tab: str, market: str, start: int = 20000, step: int = 500) -> str:
    lines = [f"# {tab}/{market}"]
    for i, name in enumerate(NAMES):
        lines.append(f"{name} {start - i * step}")
    return "\n".join(lines)


def four_blocks(**override: str) -> str:
    parts = []
    for tab, market in sd.BLOCK_ORDER:
        key = f"{tab}/{market}"
        parts.append(override.get(key) or block(tab, market))
    return "\n\n".join(parts)


def expect_stop(text: str, needle: str) -> None:
    try:
        sd.parse_backfill(text)
    except SystemExit as exc:
        assert needle in str(exc), f"다른 이유로 멈췄다: {exc}"
        return
    raise AssertionError(f"멈춰야 하는데 통과했다: {needle}")


def test_reads_four_blocks() -> None:
    parsed = sd.parse_backfill(four_blocks())
    assert set(parsed) == set(sd.BLOCK_ORDER)
    assert parsed[("외인", "KOSPI")][0] == ["종목1", 20000]
    assert len(parsed[("기관", "KOSDAQ")]) == 20


def test_name_with_spaces_keeps_its_spaces() -> None:
    # KODEX 200, 삼화 콘덴서 처럼 이름에 공백이 있는 종목이 상위에 흔하다.
    text = four_blocks(**{"외인/KOSPI": "# 외인/KOSPI\nKODEX 200 61942\n"
                          + "\n".join(f"종목{i} {5000 - i}" for i in range(1, 20))})
    parsed = sd.parse_backfill(text)
    assert parsed[("외인", "KOSPI")][0] == ["KODEX 200", 61942]


def test_comma_and_decimal_amounts() -> None:
    text = four_blocks(**{"외인/KOSPI": "# 외인/KOSPI\n가 61,942\n"
                          + "\n".join(f"종목{i} {5000 - i}.0" for i in range(1, 20))})
    parsed = sd.parse_backfill(text)
    assert parsed[("외인", "KOSPI")][0] == ["가", 61942]
    assert parsed[("외인", "KOSPI")][1] == ["종목1", 4999]


def test_missing_block_stops() -> None:
    parts = four_blocks().split("\n\n")
    expect_stop("\n\n".join(parts[:3]), "빠진 블록")


def test_short_block_stops() -> None:
    short = "# 기관/KOSDAQ\n" + "\n".join(f"종목{i} {900 - i}" for i in range(19))
    expect_stop(four_blocks(**{"기관/KOSDAQ": short}), "20종목이어야")


def test_out_of_order_stops() -> None:
    # 한 줄 건너뛰고 적으면 순서가 깨진다 — 그때 조용히 넘어가면 안 된다.
    rows = [f"종목{i} {20000 - i * 500}" for i in range(20)]
    rows[7], rows[8] = rows[8], rows[7]
    expect_stop(four_blocks(**{"외인/KOSPI": "# 외인/KOSPI\n" + "\n".join(rows)}),
                "내림차순")


def test_unknown_block_stops() -> None:
    expect_stop("# 개인/KOSPI\n가 1\n", "모르는 블록")


def test_duplicate_block_stops() -> None:
    expect_stop(four_blocks() + "\n\n" + block("외인", "KOSPI"), "두 번 나옵니다")


def test_row_before_any_header_stops() -> None:
    expect_stop("삼성전자 100\n" + four_blocks(), "머리글")


def test_non_numeric_amount_stops() -> None:
    bad = "# 외인/KOSPI\n삼성전자 백만원\n" + "\n".join(
        f"종목{i} {900 - i}" for i in range(19))
    expect_stop(four_blocks(**{"외인/KOSPI": bad}), "숫자가 아닙니다")


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
    print(f"{passed}개 통과")
