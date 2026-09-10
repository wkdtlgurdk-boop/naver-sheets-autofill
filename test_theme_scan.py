#!/usr/bin/env python3
"""theme_scan 의 그룹 배정 규칙을 가짜 스캔으로 검증한다.

여기 있는 두 규칙은 조용히 틀리는 종류다. 종목 중복 배정은 슬롯이 다 차 있어
정상으로 보이고, 이름 매칭 실패는 그룹이 그냥 갈라진 것처럼 보인다. 둘 다
시트를 열어 봐도 원인이 안 드러나므로 여기서 잡는다.

실행: python test_theme_scan.py
"""
from __future__ import annotations

import sys

import theme_scan as ts

failures: list[str] = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def stock(name, rate):
    return {"name": name, "code": name, "rate": rate, "cap": 5000, "amount": 100}


def theme(name, stocks, members=None, up=None):
    """가짜 테마. members/up 을 주면 참여율 필터가 볼 수 있는 형태가 된다."""
    t = {"theme_name": name, "theme_id": name,
         "qualifying": [stock(n, r) for n, r in stocks]}
    if members is not None:
        t["member_count"] = members
        t["up_count"] = len(stocks) if up is None else up
    return t


def group(keyword, themes):
    return {"keyword": keyword, "themes": themes}


def test_no_stock_lands_in_two_groups():
    """한 종목이 여러 테마에 속해도 슬롯은 한 번만 차지한다.

    9/7 에 서진시스템이 스마트폰·SOFC·반도체장비 세 그룹의 상위5에 동시에
    올라 8칸 중 3칸을 혼자 먹었다.
    """
    shared = ("서진시스템", 8.77)
    out = ts.build_output([
        group("A", [theme("t1", [("필옵틱스", 29.8), shared])]),
        group("B", [theme("t2", [("두산퓨얼셀", 13.8), shared])]),
    ])
    names = [[s["name"] for s in g["top5"]] for g in out]
    check("A 가 먼저 가져감", names[0], ["필옵틱스", "서진시스템"])
    check("B 는 남은 것만", names[1], ["두산퓨얼셀"])


def test_claim_goes_to_the_group_that_led():
    """선점 순서는 시트 순서(최고 상승률 내림차순)를 따른다."""
    shared = ("공유주", 9.0)
    out = ts.build_output([
        group("느린그룹", [theme("t1", [("보통주", 10.0), shared])]),
        group("빠른그룹", [theme("t2", [("급등주", 30.0), shared])]),
    ])
    check("빠른그룹이 위", [g["keyword"] for g in out], ["빠른그룹", "느린그룹"])
    check("공유주는 빠른그룹으로",
          [s["name"] for s in out[0]["top5"]], ["급등주", "공유주"])


def test_group_backfills_after_losing_a_stock():
    """뺏긴 자리는 자기 풀의 다음 종목으로 채운다 — 4개로 끝내지 않는다."""
    shared = ("공유주", 20.0)
    out = ts.build_output([
        group("선점", [theme("t1", [("일등", 30.0), shared])]),
        group("후순위", [theme("t2", [
            ("a", 9.0), ("b", 8.0), ("c", 7.0), ("d", 6.0), ("e", 5.0), shared,
        ])]),
    ])
    later = next(g for g in out if g["keyword"] == "후순위")
    check("5개를 채움", [s["name"] for s in later["top5"]],
          ["a", "b", "c", "d", "e"])


def test_group_with_nothing_left_is_dropped():
    """모든 종목을 뺏기면 빈 슬롯을 쓰지 않고 그룹째 빠진다."""
    only = ("유일주", 10.0)
    out = ts.build_output([
        group("선점", [theme("t1", [("일등", 30.0), only])]),
        group("빈그룹", [theme("t2", [only])]),
    ])
    check("빈 그룹은 출력에 없음", [g["keyword"] for g in out], ["선점"])


def test_label_is_the_biggest_theme_not_the_shortest_name():
    """자동 병합 그룹의 이름은 가장 많이 채운 테마에서 온다.

    예전에는 제일 짧은 이름을 썼다. 반도체 8개 테마가 합쳐진 그룹이 'LED'
    라는 이름으로 시트에 들어갔고, 다른 섹터로 읽혔다.
    """
    # keywords.json 에 없는 이름을 쓴다 — 있으면 오버라이드가 먼저 걸려서
    # 폴백 라벨 경로를 시험하지 못한다.
    big = theme("가상긴테마이름", [("가", 12.1), ("나", 11.3), ("다", 8.3)])
    small = theme("가상짧", [("라", 8.4)])
    final = ts.apply_keyword_overrides([[big, small]])
    check("큰 테마가 이름", [g["keyword"] for g in final], ["가상긴테마이름"])


def test_surge_lowers_the_cap_floor_but_only_for_surges():
    """20% 넘게 오른 종목은 시총 500억까지 받고, 그 아래는 2000억 그대로.

    9/8 테라뷰(+29.96%, 1,076억)와 빛과전자의 테마 동료들이 여기서 갈린다.
    완화가 상승률과 무관하게 걸리면 시총 500억대가 5% 오른 것까지 들어와
    대표주 자리를 채우므로, 두 조건은 반드시 함께 걸려야 한다.
    """
    check("급등+중소형 통과", ts.qualifies(29.96, 1076), True)
    check("급등이어도 500억 미만 탈락", ts.qualifies(29.96, 300), False)
    check("5%대는 완화 없음", ts.qualifies(5.54, 577), False)
    check("5%대 대형주는 통과", ts.qualifies(5.54, 4102), True)
    check("20% 경계 포함", ts.qualifies(20.0, 500), True)
    check("5% 경계 포함", ts.qualifies(5.0, 2000), True)
    check("4.9% 는 탈락", ts.qualifies(4.9, 99999), False)


def test_theme_list_reads_every_page():
    """목록은 마지막 페이지가 반복될 때까지 읽는다 — 1페이지 40개가 아니다.

    네이버 목록에는 페이지 링크가 없어서 끝을 셀 수 없고, 범위를 넘기면 같은
    페이지를 다시 준다. page=1 만 읽던 시절 266개 중 40개(15%)만 스캔했고
    9/8 에 제약업체 테마가 통째로 빠졌다.
    """
    pages = {
        1: [("1", "가"), ("2", "나")],
        2: [("3", "다")],
        3: [("3", "다")],          # 범위 초과 — 마지막 페이지 반복
    }
    seen_urls = []

    def fake_fetch(_opener, url):
        seen_urls.append(url)
        page = int(url.rsplit("page=", 1)[1])
        return "".join(
            f'sise_group_detail.naver?type=theme&no={tid}">{nm}</a>'
            for tid, nm in pages.get(page, pages[3])
        )

    real_fetch, real_sleep = ts.fetch, ts.time.sleep
    ts.fetch, ts.time.sleep = fake_fetch, lambda _s: None
    try:
        got = ts.fetch_theme_list(object())
    finally:
        ts.fetch, ts.time.sleep = real_fetch, real_sleep

    check("전 페이지 수집", got, [("1", "가"), ("2", "나"), ("3", "다")])
    check("반복 페이지에서 멈춤", len(seen_urls), 3)


def test_exact_theme_name_beats_a_shorter_substring():
    """'LED장비' 가 'LED' 패턴에 먹히지 않는다.

    keywords.json 은 정확한 테마명을 담고, 한쪽이 다른 쪽의 접두사인 경우가
    흔하다. 부분문자열만 보면 먼저 검사된 키워드가 긴 이름을 가져가 버린다.
    """
    patterns = {"대표주": ["LED"], "장비": ["LED장비"]}
    check("정확 일치 우선", ts.keyword_for("LED장비", patterns), "장비")
    check("정확 일치 자기 자신", ts.keyword_for("LED", patterns), "대표주")


def test_stale_theme_never_reaches_a_group():
    """제외 목록에 적힌 테마는 부분 문자열로 걸려 통째로 빠진다.

    네이버의 정식 이름은 '일본 수출규제(국산화)' 처럼 괄호가 붙는다. 목록에
    적는 사람이 그 표기를 정확히 옮길 거라고 보면 조용히 안 걸린다.
    """
    ex = {"themes": [{"pattern": "일본 수출규제", "reason": "2019년 이벤트"}],
          "stocks": []}
    kept = ts.filter_themes([
        theme("일본 수출규제(국산화)", [("가", 9.0), ("나", 8.0), ("다", 7.0)]),
        theme("반도체 장비", [("라", 9.0), ("마", 8.0), ("바", 7.0)]),
    ], ex)
    check("옛 테마만 빠짐", [t["theme_name"] for t in kept], ["반도체 장비"])


def test_stock_is_dropped_only_from_the_theme_it_does_not_fit():
    """범한퓨얼셀은 조선기자재에서만 빠지고 연료전지에서는 남는다.

    전역으로 빼면 정작 그 종목이 진짜로 대표하는 테마에서도 사라진다.
    """
    ex = {"themes": [], "stocks": [
        {"name": "범한퓨얼셀", "themes": ["조선"], "reason": "수소 쪽"}]}
    kept = ts.filter_themes([
        theme("조선기자재", [("범한퓨얼셀", 20.0), ("가", 9.0), ("나", 8.0), ("다", 7.0)]),
        theme("연료전지", [("범한퓨얼셀", 20.0), ("라", 9.0), ("마", 8.0)]),
    ], ex)
    names = {t["theme_name"]: [s["name"] for s in t["qualifying"]] for t in kept}
    check("조선기자재에서는 빠짐", names["조선기자재"], ["가", "나", "다"])
    check("연료전지에는 남음", names["연료전지"], ["범한퓨얼셀", "라", "마"])


def test_stock_without_themes_is_dropped_everywhere():
    ex = {"themes": [], "stocks": [{"name": "피노", "reason": "통신 아님"}]}
    kept = ts.filter_themes([
        theme("통신장비", [("피노", 20.0), ("가", 9.0), ("나", 8.0), ("다", 7.0)]),
    ], ex)
    check("전역 제외", [s["name"] for s in kept[0]["qualifying"]], ["가", "나", "다"])


def test_theme_dies_when_exclusions_take_it_below_min_count():
    """엉뚱한 종목 둘을 빼고 나면 애초에 테마가 움직인 게 아니었다."""
    ex = {"themes": [], "stocks": [
        {"name": "피노", "reason": "통신 아님"},
        {"name": "범한퓨얼셀", "themes": ["통신"], "reason": "수소 쪽"}]}
    kept = ts.filter_themes([
        theme("통신장비", [("피노", 20.0), ("범한퓨얼셀", 18.0), ("가", 9.0)]),
    ], ex)
    check("테마째 빠짐", kept, [])


def test_wide_stale_theme_with_a_few_movers_is_dropped():
    """구성 40종목 중 3개만 오른 테마는 테마가 움직인 게 아니다.

    이름을 모르는 옛 테마도 대개 이 모양으로 걸린다 — 종목은 많고 그중
    몇 개만 오늘 각자의 이유로 오른다. 제외 목록이 이름을 못 따라가도
    여기서 한 겹 걸러진다.
    """
    empty = {"themes": [], "stocks": []}
    wide = theme("옛날테마", [("가", 9.0), ("나", 8.0), ("다", 7.0)], members=40, up=3)
    tight = theme("오늘테마", [("라", 9.0), ("마", 8.0), ("바", 7.0)], members=8, up=6)
    kept = ts.filter_themes([wide, tight], empty)
    check("좁고 뜨거운 것만 남음", [t["theme_name"] for t in kept], ["오늘테마"])


def test_participation_floor_is_skipped_when_the_count_is_unknown():
    """member_count 가 없는 옛 raw_scan.json 을 죽이지 않는다.

    --regroup 은 디스크에 있는 스캔을 다시 읽는다. 2026-09-10 이전 스캔에는
    구성종목 수가 없고, 모르는 값으로 테마를 지우면 그날 시트가 얇아진다.
    """
    empty = {"themes": [], "stocks": []}
    old = theme("옛스캔", [("가", 9.0), ("나", 8.0), ("다", 7.0)])
    check("모르면 통과", [t["theme_name"] for t in ts.filter_themes([old], empty)],
          ["옛스캔"])


def test_filter_does_not_mutate_the_scan_it_was_given():
    """raw_scan.json 은 거르기 전을 담는다 — 제외가 무엇을 지웠는지 남아야 한다."""
    ex = {"themes": [], "stocks": [{"name": "피노", "reason": "통신 아님"}]}
    src = [theme("통신장비", [("피노", 20.0), ("가", 9.0), ("나", 8.0), ("다", 7.0)])]
    ts.filter_themes(src, ex)
    check("원본 그대로", [s["name"] for s in src[0]["qualifying"]],
          ["피노", "가", "나", "다"])


def test_shipped_exclusion_file_is_readable():
    """리포에 든 exclusions.json 이 실제로 파싱되고 규칙이 걸린다."""
    ex = ts.load_exclusions()
    check("테마 규칙 있음", bool(ex["themes"]), True)
    check("종목 규칙 있음", bool(ex["stocks"]), True)
    check("일 수출규제가 걸림",
          bool(ts.theme_exclusion("일본 수출규제(국산화)", ex["themes"])), True)
    check("5G 가 걸림",
          bool(ts.theme_exclusion("5G(5세대 이동통신)", ex["themes"])), True)
    check("반도체 장비는 안 걸림",
          ts.theme_exclusion("반도체 장비", ex["themes"]), None)
    beomhan = {"name": "범한퓨얼셀", "code": "382900"}
    check("범한퓨얼셀 @ 조선기자재 걸림",
          bool(ts.stock_exclusion(beomhan, "조선기자재", ex["stocks"])), True)
    check("범한퓨얼셀 @ 연료전지 안 걸림",
          ts.stock_exclusion(beomhan, "연료전지", ex["stocks"]), None)


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
