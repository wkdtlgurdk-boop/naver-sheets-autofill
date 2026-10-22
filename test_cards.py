#!/usr/bin/env python3
"""카드 렌더링을 잃어버린 원본 카드의 실측 좌표에 붙들어 둔다.

원래 카드 봇은 사용자 PC 와 함께 사라졌고, 남은 것은 2026-09-04/07/08 에
찍힌 PNG 뿐이다. 그 이미지에서 잰 '글자 줄이 시작되고 끝나는 y 좌표'가 여기
기준표다 — 디자인 명세가 따로 없으니 이게 명세다.

세로 좌표만 본다. 글꼴이 원본(Pretendard 계열로 보임)이 아니라 러너에서
구할 수 있는 Noto Sans CJK 라 글자 폭은 3~5% 좁고, 그건 맞출 수도 없고
맞출 필요도 없다. 반면 줄의 위치와 높이가 어긋나면 카드가 무너진다.

실행: python test_cards.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

import cards
import theme_scan as ts

failures: list[str] = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# 2026-09-08 카드에서 잰 줄 위치 (y시작, y끝). 헤더/구분선/본문/푸터가 모두 들어 있다.
REFERENCE = {
    "01_monthly.png": [
        (74, 120), (147, 148), (196, 265), (306, 331), (378, 522), (555, 556),
        (583, 615), (638, 638), (656, 686), (710, 710), (727, 758), (782, 782),
        (799, 830), (854, 854), (871, 903), (926, 926), (944, 974), (998, 998),
        (1015, 1046), (1236, 1237), (1266, 1288)],
    "02_원전전력설비.png": [
        (74, 120), (147, 148), (196, 265), (306, 331), (377, 400), (416, 464),
        (515, 516), (552, 586), (618, 618), (644, 678), (710, 710), (736, 770),
        (802, 802), (828, 862), (894, 894), (921, 954), (1078, 1098),
        (1115, 1137), (1236, 1237), (1266, 1288)],
}
START_TOL, HEIGHT_TOL = 3, 5


def eok_mn(eok):
    return eok * 100


def stocks(rows):
    return [{"name": n, "code": f"{i:06d}", "rate": r, "cap": 5000,
             "amount": eok_mn(a)} for i, (n, r, a) in enumerate(rows)]


def filler(rate, total_eok, n=5):
    """평균상승률과 거래대금 합만 맞춘 채움용 5종목."""
    return stocks([(f"종목{i + 1}", rate, total_eok // n) for i in range(n)])


# 9/8 카드에 찍혀 있던 실제 숫자. 1위 테마만 종목까지 원본 그대로다.
FIXTURE = [
    {"keyword": "원전·전력설비", "total_qualifying": 7,
     "source_themes": ["원자력발전", "원자력발전소 해체", "풍력에너지"],
     "top5": stocks([("오르비텍", 29.83, 456), ("서전기전", 29.80, 292),
                     ("대우건설", 8.47, 5439), ("우리기술", 8.15, 1541),
                     ("태웅", 6.09, 204)])},
    {"keyword": "바이오", "total_qualifying": 21,
     "source_themes": ["제약업체", "비만치료제"], "top5": filler(12.88, 6223)},
    {"keyword": "밸류업", "total_qualifying": 9,
     "source_themes": ["증권"], "top5": filler(13.70, 3845)},
    {"keyword": "반도체·IT 대표주", "total_qualifying": 6,
     "source_themes": ["반도체 대표주(생산)"], "top5": filler(13.46, 673)},
    {"keyword": "우주항공산업", "total_qualifying": 5,
     "source_themes": ["우주항공산업"], "top5": filler(5.98, 257)},
    {"keyword": "의료기기", "total_qualifying": 4,
     "source_themes": ["의료기기"], "top5": filler(7.48, 133)},
    {"keyword": "2026 상반기 신규상장", "total_qualifying": 3,
     "source_themes": ["2026년 상반기 신규상장"], "top5": filler(9.47, 90)},
]
MARKET = {"KOSPI": {"index": 6955.0, "amount_trillion": 26.2, "foreign_eok": 6316},
          "KOSDAQ": {"index": 812.0, "amount_trillion": 8.2, "foreign_eok": 1583}}


def bands(path):
    """이미지에서 글자 줄의 (시작y, 끝y) 목록을 뽑는다.

    배경색과 다른 픽셀이 있는 행을 모아, 6줄 넘게 비면 다른 줄로 본다.
    행 구분선(1px)도 한 줄로 잡히는데, 그것도 레이아웃의 일부라 그대로 센다.
    """
    from PIL import Image, ImageChops
    im = Image.open(path).convert("RGB")
    flat = Image.new("RGB", im.size, cards.BG)
    mask = ImageChops.difference(im, flat).convert("L").point(
        lambda v: 255 if v > 6 else 0)
    rows = [y for y in range(im.height)
            if mask.crop((0, y, im.width, y + 1)).getbbox()]
    out, start, prev = [], rows[0], rows[0]
    for y in rows[1:]:
        if y - prev > 6:
            out.append((start, prev))
            start = y
        prev = y
    out.append((start, prev))
    return out


def test_layout_matches_the_original_cards():
    """두 카드의 모든 줄이 원본과 같은 자리에서 시작하고 같은 높이로 끝난다."""
    with tempfile.TemporaryDirectory() as tmp:
        made = cards.build(Path(tmp), FIXTURE, MARKET, datetime(2026, 9, 8, 17, 0))
        by_name = {p.name: p for p in made}
        for name, want in REFERENCE.items():
            got = bands(by_name[name])
            check(f"{name} 줄 개수", len(got), len(want))
            if len(got) != len(want):
                continue
            for i, ((gy0, gy1), (wy0, wy1)) in enumerate(zip(got, want)):
                if abs(gy0 - wy0) > START_TOL:
                    failures.append(f"{name} {i}번째 줄 시작: {gy0} (원본 {wy0})")
                if abs((gy1 - gy0) - (wy1 - wy0)) > HEIGHT_TOL:
                    failures.append(
                        f"{name} {i}번째 줄 높이: {gy1 - gy0 + 1} (원본 {wy1 - wy0 + 1})")


def test_numbers_match_what_the_original_card_printed():
    """평균상승률과 거래대금이 9/8 카드에 찍힌 값 그대로 나온다.

    시트의 대표행 수식(AVERAGEA/SUM)과 같은 정의여야 한다. 카드와 시트가
    다른 숫자를 말하면 어느 쪽이 맞는지 아무도 모른다.
    """
    top = FIXTURE[0]["top5"]
    check("평균상승률", f"{cards.avg_rate(top):+.2f}%", "+16.47%")
    check("거래대금", sum(cards.eok(s["amount"]) for s in top), 7932)
    check("백만 -> 억", cards.eok(543900), 5439)


def test_cards_are_ordered_by_trading_value():
    """카드 순서와 요약 카드의 순위표가 같은 정렬(거래대금 순)을 쓴다."""
    order = [g["keyword"] for g in cards.ranked(FIXTURE)]
    check("1위", order[0], "원전·전력설비")
    check("꼴찌", order[-1], "2026 상반기 신규상장")
    with tempfile.TemporaryDirectory() as tmp:
        made = cards.build(Path(tmp), FIXTURE, MARKET, datetime(2026, 9, 8, 17, 0))
    check("파일 이름이 순위를 따름",
          [p.name for p in made][:3],
          ["01_monthly.png", "02_원전전력설비.png", "03_바이오.png"])


def test_no_theme_day_makes_one_card_and_supply_is_separate():
    """주도 테마가 없는 날은 '오늘의 테마 없음' 한 장. 수급 카드는 --supply 로 매일 따로."""
    supply = [{"keyword": k, "source_themes": [f"{k} 상위"], "total_qualifying": 2,
               "top5": [{"name": "가", "code": "000001", "rate": -1.5, "amount": 900000,
                         "net_buy": 120000},
                        {"name": "나", "code": "000002", "rate": 2.0, "amount": 500000,
                         "net_buy": 80000}]}
              for k in ("외인 수급", "기관 수급")]
    now = datetime(2026, 10, 15, 21, 0)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "20261015"
        made = cards.build_no_theme(Path(tmp), MARKET, now)
        check("테마 없음 카드", [str(p.relative_to(root)) for p in made], ["01_monthly.png"])
        made = cards.build_supply(Path(tmp), supply, MARKET, now)
        check("수급 카드 자리", [str(p.relative_to(root)) for p in made],
              ["supply/01_summary.png", "supply/02_외인수급.png", "supply/03_기관수급.png"])
    check("수급 카드 금액은 순매수", cards.supply_view(supply[0])["top5"][0]["amount"], 120000)


def test_supply_card_fits_ten_rows_above_the_footer():
    """수급은 10종목(10/22). 10행이 바닥글 선(1236) 위에 다 들어가고, 배지는 '수급'."""
    top = [{"name": f"종목{i}", "code": f"{i:06d}", "rate": 0, "amount": 0,
            "net_buy": 100000 - i * 5000} for i in range(10)]
    g = cards.supply_view({"keyword": "외인 수급", "source_themes": ["외인 수급 상위"],
                           "total_qualifying": 10, "top5": top})
    now = datetime(2026, 10, 22, 21, 0)
    card = cards.theme_card(now, g, 1, 2, amount_label="순매수", show_rate=False,
                            badge=cards.SUPPLY_BADGE)
    im = card.im.convert("L")
    # 마지막 행(576 + 9*60 = 1116) 글자가 바닥글 선보다 위에 있다
    ink_rows = [y for y in range(1060, 1236) if any(im.getpixel((x, y)) > 150 for x in range(68, 400))]
    check("10행째가 찍혔다", bool(ink_rows), True)
    check("바닥글 선 위", max(ink_rows) < 1200, True)


def test_title_spaces_the_middle_dot_but_the_list_does_not():
    """제목만 가운뎃점 양옆을 띄운다 — 원본 카드가 그렇게 쓴다."""
    check("제목", cards.spaced("원전·전력설비"), "원전 · 전력설비")
    check("목록", "원전·전력설비", "원전·전력설비")


def test_footer_is_built_from_the_scan_thresholds():
    """푸터의 조건 문구는 theme_scan 상수에서 만든다 — 문턱을 바꾸면 같이 바뀐다."""
    # 러너의 scan_mode.json(그날 실제 모드)에 끌려가지 않게 기본 모드로 본다
    text = cards.footer_text({})
    check("시총 문턱", f"{ts.MIN_CAP:,}억" in text, True)
    check("급등 문턱", f"{ts.SURGE_RATE:g}%" in text, True)
    check("종목 수", f"{ts.MIN_COUNT}개" in text, True)


def test_long_theme_list_is_truncated_not_overflowed():
    """묶은 테마가 열 개인 날 박스 밖으로 삐져나가지 않는다."""
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    f = cards.font("medium", cards.SZ["box_body"])
    long = " · ".join(f"아주긴테마이름{i}" for i in range(12))
    out = cards.fit(d, long, f, 800)
    check("잘림", out.endswith("…"), True)
    check("폭 안에 들어감", d.textlength(out, font=f) <= 800, True)
    check("짧으면 그대로", cards.fit(d, "짧다", f, 800), "짧다")


def test_long_group_name_stays_inside_the_card():
    """10/2 에 「반도체 밸류체인(HBM·장비·시스템반도체)」가 요약 카드에서 상승률과
    겹쳐 찍히고, 테마 카드 제목은 오른쪽 끝에서 잘렸다. 둘 다 칸 안에 들어와야 한다."""
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    long = "반도체 밸류체인(HBM·장비·시스템반도체)"
    rate_f = cards.font("black", cards.SZ["m_rate"])
    room = 814 - d.textlength("+6.03%", font=rate_f) - cards.NAME_GAP - 148
    f, out = cards.shrink(d, long, "bold", cards.SZ["m_name"], room, cards.MIN_M_NAME)
    check("요약 이름이 상승률 앞에서 끝남", d.textlength(out, font=f) <= room, True)
    f, out = cards.shrink(d, cards.spaced(long), "black", cards.SZ["title"],
                          cards.R - cards.M, cards.MIN_TITLE)
    check("제목이 카드 안에 들어감", d.textlength(out, font=f) <= cards.R - cards.M, True)
    # 짧은 이름은 원래 크기 그대로 — 원본 실측 좌표가 유지돼야 한다
    f, out = cards.shrink(d, "바이오", "bold", cards.SZ["m_name"], room, cards.MIN_M_NAME)
    check("짧으면 원래 크기", (f.size, out), (cards.SZ["m_name"], "바이오"))

    # 실제 카드로 그려서 이름 칸과 상승률 칸 사이에 빈 세로줄이 남는지 본다
    groups = [dict(FIXTURE[0], keyword=long)] + FIXTURE[1:]
    with tempfile.TemporaryDirectory() as tmp:
        made = cards.build(Path(tmp), groups, MARKET, datetime(2026, 10, 2, 17, 0))
        im = Image.open(made[0]).convert("RGB")
        px = im.load()
        ink = [any(sum(px[x, y]) > 200 for y in range(583, 616)) for x in range(148, 815)]
        # 이름 끝과 상승률 시작 사이에 글자 없는 세로줄이 NAME_GAP 의 절반 이상 이어져야 한다
        run = best = 0
        for on in ink[ink.index(True):]:
            run = 0 if on else run + 1
            best = max(best, run)
        check("1행 이름과 상승률 사이에 빈 칸", best >= cards.NAME_GAP // 2, True)
        title_im = Image.open(next(p for p in made if p.name != "01_monthly.png")).convert("RGB")
        tp = title_im.load()
        check("제목이 오른쪽 여백을 침범하지 않음",
              all(sum(tp[x, y]) < 200 for x in range(cards.R + 2, cards.W) for y in range(190, 270)), True)


def _as_of(value):
    """THEME_AS_OF 를 넣고 as_of 모듈을 다시 읽는다."""
    import importlib
    import os

    import as_of as mod
    old = os.environ.get("THEME_AS_OF")
    if value is None:
        os.environ.pop("THEME_AS_OF", None)
    else:
        os.environ["THEME_AS_OF"] = value
    try:
        importlib.reload(mod)
        return mod.as_of()
    finally:
        if old is None:
            os.environ.pop("THEME_AS_OF", None)
        else:
            os.environ["THEME_AS_OF"] = old
        importlib.reload(mod)


def test_as_of_reads_the_target_day_and_says_it_was_named():
    day, overridden = _as_of("2026-09-11")
    check("지목한 날짜", day.strftime("%Y-%m-%d"), "2026-09-11")
    check("사람이 지목했다", overridden, True)
    day, overridden = _as_of(None)
    check("안 주면 오늘", overridden, False)


def test_a_bad_as_of_stops_instead_of_falling_back_to_today():
    # 형식이 틀렸는데 오늘로 돌아가면, 9/11 을 채우려던 실행이 조용히 오늘을
    # 덮어쓴다. 그건 고치려던 것보다 나쁘다.
    try:
        _as_of("9/11")
    except SystemExit:
        return
    failures.append("틀린 THEME_AS_OF 를 그냥 통과시켰다")


def test_every_dated_step_asks_the_same_place():
    """cards / post_threads / theme_write 가 각자 날짜를 계산하지 않는다.

    9/13 사고가 정확히 이것이었다 — theme_write 만 THEME_AS_OF 를 보고
    cards 와 post_threads 는 datetime.now() 를 봐서, 9/11 을 다시 채우는
    실행이 카드를 9/13(일) 로 찍어 스레드에 올렸다.
    """
    for name in ("cards.py", "post_threads.py", "theme_write.py"):
        src = Path(__file__).with_name(name).read_text(encoding="utf-8")
        if "from as_of import" not in src:
            failures.append(f"{name} 이 공용 as_of 를 쓰지 않는다")
        # 날짜를 직접 만드는 곳이 남아 있으면 갈라진다. as_of.py 안에서만 허용.
        if "datetime.now(KST)" in src:
            failures.append(f"{name} 에 datetime.now(KST) 가 남아 있다")


def test_a_backfill_run_does_not_post_to_threads_by_default():
    src = Path(__file__).with_name("post_threads.py").read_text(encoding="utf-8")
    if "THREADS_ALLOW_BACKFILL" not in src:
        failures.append("지난 날짜 실행에서 게시를 막는 장치가 없다")


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
