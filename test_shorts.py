#!/usr/bin/env python3
"""쇼츠 영상과 유튜브 업로드 문구를 붙들어 둔다.

영상 길이는 고정이 아니다 — 테마 카드 한 장을 3초씩 보여 주므로 테마 수에 따라
늘고 준다. 그 계산, 표지에서 행이 한 줄씩 들어오는지, 업로드 제목·중복 표식을 본다.
렌더링은 Pillow 와 한글 폰트가 있는 러너에서만 돈다(카드 테스트와 같다).

실행: python test_shorts.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import cards
import shorts
import youtube_upload as yt
from test_cards import FIXTURE, MARKET

failures: list[str] = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def test_length_follows_the_number_of_themes():
    """테마마다 3초. 표지는 행 수만큼 길어진다."""
    for n in (3, 8, 12):
        tl = shorts.Timeline(n)
        check(f"{n}개 표지", round(tl.cover, 2), round(shorts.T_HEAD + n * shorts.T_ROW + shorts.HOLD, 2))
        check(f"{n}개 전체", round(tl.total, 2), round(tl.cover + 3.0 * n, 2))
        check(f"{n}개 마지막 장 시작", round(tl.start(n), 2), round(tl.total - 3.0, 2))
    check("테마 한 장", shorts.THEME_SECONDS, 3.0)
    tl = shorts.Timeline(8)
    check("8개면 30초 안쪽", 25 < tl.total < 30, True)
    check("첫 프레임은 표지", tl.scene(0), 0)
    check("표지 끝나면 1번 테마", tl.scene(tl.cover + 0.01), 1)
    check("마지막 순간은 마지막 테마", tl.scene(tl.total - 0.01), 8)


def test_cover_rows_come_in_one_at_a_time():
    """표지에서 1행은 떠 있고 아직 차례가 안 온 아래 행은 비어 있다."""
    with tempfile.TemporaryDirectory() as tmp:
        made = cards.build(Path(tmp), FIXTURE, MARKET, datetime(2026, 9, 8, 17, 0))
        s = shorts.Shorts(made, "2026.09.08 (월)")
        t = shorts.T_HEAD + shorts.T_ROW_IN + 0.05          # 1행만 다 들어온 시각
        im = s.frame(t)
        row = lambda i: im.crop((0, shorts.CARD_Y + shorts.ROW_TOP + i * shorts.ROW_STEP,
                                 1080, shorts.CARD_Y + shorts.ROW_TOP + (i + 1) * shorts.ROW_STEP))
        lit = lambda r: max(sum(p) for p in r.getdata())
        check("1행 보임", lit(row(0)) > 400, True)
        check("마지막 행 아직 없음", lit(row(len(made) - 2)) < 120, True)
        full = s.frame(s.tl.cover - 0.05)
        check("표지 끝엔 마지막 행도 보임",
              max(sum(p) for p in full.crop((0, shorts.CARD_Y + shorts.ROW_TOP + (len(made) - 2) * shorts.ROW_STEP,
                                             1080, shorts.CARD_Y + shorts.ROW_TOP + (len(made) - 1) * shorts.ROW_STEP)).getdata()) > 400,
              True)
        check("영상 크기", im.size, (1080, 1920))


def test_youtube_title_and_duplicate_mark():
    now = datetime(2026, 10, 2, 17, 0)
    # 러너의 scan_mode.json(그날 실제 모드)에 끌려가지 않게 모드를 고정한다
    real = cards.no_theme_day
    cards.no_theme_day = lambda: True
    try:
        t = yt.title(FIXTURE, now)
    finally:
        cards.no_theme_day = real
    check("테마 없는 날 제목", "외인·기관 순매수" in t and "#shorts" in t, True)
    cards.no_theme_day = lambda: False
    try:
        t = yt.title(FIXTURE, now)
    finally:
        cards.no_theme_day = real
    check("제목은 날짜 표식으로 시작", t.startswith(yt.date_mark(now)), True)
    check("제목 길이", len(t) <= yt.TITLE_LIMIT, True)
    check("#shorts", "#shorts" in t, True)
    check("1위 테마가 제목에", cards.ranked(FIXTURE)[0]["keyword"] in t, True)
    d = yt.description(FIXTURE, now)
    check("설명에 순위 목록", f"1. {cards.ranked(FIXTURE)[0]['keyword']}" in d, True)
    check("설명에 고지", "투자 권유가 아닌" in d, True)


def test_youtube_tags_are_few():
    """고정 5개 + 상위 테마 4개 (2026-10-06 사용자 결정)."""
    groups = [{"keyword": f"테마{i}(설명)", "top5": [{"name": f"종목{i}", "rate": 5,
                                                   "amount": 1000 - i}]} for i in range(8)]
    t = yt.tags(groups)
    check("태그 9개", len(t), 9)
    check("고정 태그", t[:5], ["주식", "테마주", "오늘의테마", "장마감", "특징주"])
    check("괄호 앞 이름", t[5], "테마0")


def test_upload_constants_exist():
    """10/7 CATEGORY 를 지워 업로드가 NameError 로 건너뛰어졌다. 상수가 다 있는지 본다."""
    for name in ("CATEGORY", "TOKEN_URL", "API", "UPLOAD", "TITLE_LIMIT", "DESC_LIMIT"):
        check(f"{name} 있음", hasattr(yt, name), True)

def test_missing_secrets_skip_without_failing():
    import os
    old = {k: os.environ.pop(k, None) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")}
    try:
        check("시크릿 없으면 0 으로 끝남", yt.main(), 0)
    finally:
        for k, v in old.items():
            if v is not None:
                os.environ[k] = v


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{t.__name__} 예외: {exc!r}")
    if failures:
        print(f"실패 {len(failures)}건 / 전체 {len(tests)}건\n")
        for f in failures:
            print("  " + f)
        return 1
    print(f"통과 {len(tests)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
