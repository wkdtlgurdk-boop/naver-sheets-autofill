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


def describe(labels: list[str], label: str) -> str:
    """write_day 의 로그 한 줄이 무엇을 말하는지 그대로 재현한다."""
    idx, shift = sd.plan_slots(labels, label)
    dropped = labels[0] if shift else None
    overwrote = labels[idx] == label
    if shift:
        labels = labels[1:] + [""]
    labels[idx] = label
    return (f"한 칸 밀어냄({dropped} 버림)" if shift
            else "이미 있던 칸 덮어씀" if overwrote
            else "빈 칸에 추가")


def test_log_says_overwrote_when_the_day_is_already_there() -> None:
    # 하루 세 번 시도하는 구조라 두 번째·세 번째 실행은 같은 칸을 덮어쓴다.
    # 그것이 안전하다는 증거가 이 로그 한 줄뿐이므로, 여기서 '빈 칸에 추가'
    # 라고 말하면 창이 밀렸는지 아닌지를 로그로 판별할 수 없게 된다.
    assert describe(["9/9", "9/10", "9/11", "9/14", "9/15"], "9/15") == \
        "이미 있던 칸 덮어씀"


def test_log_says_shifted_when_the_window_is_full() -> None:
    assert describe(["9/8", "9/9", "9/10", "9/11", "9/14"], "9/15") == \
        "한 칸 밀어냄(9/8 버림)"


def test_log_says_added_when_a_slot_is_free() -> None:
    assert describe(["9/9", "9/10", "", "", ""], "9/11") == "빈 칸에 추가"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
    print(f"{passed}개 통과")
