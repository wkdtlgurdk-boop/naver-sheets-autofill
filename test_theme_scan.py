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

# 오배정 판정(theme_fit)은 저장소의 affinity.json 을 읽는다. 여기 시험은 실제
# 종목명으로 배정 순서를 보므로, 그 기록이 끼어들지 않게 비워 둔다. 오배정
# 자체는 test_theme_fit.py 가 본다.
ts.load_affinity = lambda: {}

failures: list[str] = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def stock(name, rate):
    return {"name": name, "code": name, "rate": rate, "cap": 5000, "amount": 50_000}


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


def filler(prefix, n, base=1.0):
    """자리를 채우는 종목들. 배정 시험이 MIN_COUNT 에 걸리지 않게 한다.

    등락률을 낮게 줘서 top5 의 앞자리를 차지하지 않는다 -- 시험이 보려는
    것은 뺏고 뺏기는 순서이지 이 종목들이 아니다.
    """
    return [(f"{prefix}{i}", base - i * 0.01) for i in range(n)]


def test_no_stock_lands_in_two_groups():
    """한 종목이 여러 테마에 속해도 슬롯은 한 번만 차지한다.

    9/7 에 서진시스템이 스마트폰·SOFC·반도체장비 세 그룹의 상위5에 동시에
    올라 8칸 중 3칸을 혼자 먹었다.
    """
    shared = ("서진시스템", 8.77)
    out = ts.build_output([
        group("A", [theme("t1", [("필옵틱스", 29.8), shared] + filler("a", 3))]),
        group("B", [theme("t2", [("두산퓨얼셀", 13.8), shared] + filler("b", 3))]),
    ])
    names = [[s["name"] for s in g["top5"]] for g in out]
    check("A 가 먼저 가져감", names[0][:2], ["필옵틱스", "서진시스템"])
    check("B 는 남은 것만", "서진시스템" in names[1], False)
    check("B 도 자기 것으로 채움", names[1][0], "두산퓨얼셀")


def test_claim_goes_to_the_group_that_led():
    """선점 순서는 시트 순서(최고 상승률 내림차순)를 따른다."""
    shared = ("공유주", 9.0)
    out = ts.build_output([
        group("느린그룹", [theme("t1", [("보통주", 10.0), shared] + filler("s", 3))]),
        group("빠른그룹", [theme("t2", [("급등주", 30.0), shared] + filler("f", 3))]),
    ])
    check("빠른그룹이 위", [g["keyword"] for g in out], ["빠른그룹", "느린그룹"])
    check("공유주는 빠른그룹으로",
          [s["name"] for s in out[0]["top5"]][:2], ["급등주", "공유주"])


def test_group_backfills_after_losing_a_stock():
    """뺏긴 자리는 자기 풀의 다음 종목으로 채운다 — 4개로 끝내지 않는다."""
    shared = ("공유주", 20.0)
    out = ts.build_output([
        group("선점", [theme("t1", [("일등", 30.0), shared] + filler("p", 3))]),
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
        group("선점", [theme("t1", [("일등", 30.0), only] + filler("p", 3))]),
        group("빈그룹", [theme("t2", [only])]),
    ])
    check("빈 그룹은 출력에 없음", [g["keyword"] for g in out], ["선점"])


def test_group_below_min_count_after_claims_is_dropped():
    """배정 뒤에 3종목이 안 되면 그룹째 뺀다.

    MIN_COUNT 는 병합 전 테마마다 보는 값이라, 앞 그룹이 종목을 먼저 가져가면
    그 밑으로 내려앉는다. 2026-09-16 에 철도·디스플레이폰부품·2차전지가 나란히
    2종목으로 시트에 올라갔다 -- '조건 충족 3개 이상인 테마'라고 해 놓고
    2개짜리 슬롯을 내보내면 그 말이 아니게 된다.
    """
    a, b = ("공유A", 20.0), ("공유B", 19.0)
    out = ts.build_output([
        group("선점", [theme("t1", [("일등", 30.0), a, b] + filler("p", 2))]),
        group("굶은그룹", [theme("t2", [a, b, ("자기것", 5.0)])]),
    ])
    check("2종목짜리는 빠짐", [g["keyword"] for g in out], ["선점"])


def test_dropped_group_gives_its_stocks_back():
    """빠진 그룹이 잡고 있던 종목은 뒤 그룹이 다시 쓸 수 있어야 한다."""
    shared = ("공유주", 25.0)
    out = ts.build_output([
        group("굶을그룹", [theme("t1", [shared, ("둘째", 24.0)])]),
        group("뒤그룹", [theme("t2", [shared] + filler("z", 3, base=20.0))]),
    ])
    check("굶은 그룹은 빠짐", [g["keyword"] for g in out], ["뒤그룹"])
    check("공유주가 뒤그룹으로 돌아감",
          "공유주" in [s["name"] for s in out[0]["top5"]], True)


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


def test_three_thresholds_each_do_their_own_job():
    """문턱이 셋이고 각각 다른 구간을 맡는다.

    9/8 테라뷰(+29.96%, 1,076억)와 9/11 아바텍(+14.02%, 1,412억)이 여기서
    갈린다. 완화가 상승률과 무관하게 걸리면 시총 500억대가 5% 오른 것까지
    들어와 대표주 자리를 채우므로, 각 조건은 반드시 짝으로 걸려야 한다.
    """
    check("대형주 일반", ts.qualifies(5.54, 4102), True)
    check("5% 경계 포함", ts.qualifies(5.0, 2000), True)
    check("4.9% 는 탈락", ts.qualifies(4.9, 99999), False)
    check("5%대 중형주는 탈락", ts.qualifies(5.54, 577), False)

    check("급등+소형 통과", ts.qualifies(29.96, 1076), True)
    check("20% 경계 포함", ts.qualifies(20.0, 500), True)
    check("급등이어도 500억 미만 탈락", ts.qualifies(29.96, 300), False)


def test_the_middle_tier_catches_the_gap_that_dropped_mlcc():
    """+10~15% 구간의 1,000~2,000억 종목을 버리지 않는다.

    2026-09-11 MLCC 테마는 등락률 1위(+7.69%)였는데도 조건충족 2종목으로
    탈락했다. 아바텍(+14.02%, 1,412억)이 시총에서 걸렸기 때문이다. 중간
    문턱을 더해 그 한 종목이 들어오면 3종목이 되어 테마가 산다.
    """
    check("아바텍 통과", ts.qualifies(14.02, 1412), True)
    check("12% 경계 포함", ts.qualifies(12.0, 1000), True)
    check("11.9% 는 탈락", ts.qualifies(11.9, 1999), False)
    check("12% 여도 1,000억 미만 탈락", ts.qualifies(12.0, 999), False)
    # 코칩은 시총이 문턱을 넘지만 상승률이 12% 에 못 미쳐 여전히 탈락한다.
    check("코칩은 그대로 탈락", ts.qualifies(10.36, 1868), False)

    mlcc = [(29.98, 2826), (18.76, 14283), (14.02, 1412), (10.36, 1868),
            (8.67, 678), (3.65, 727), (0.0, 1045712), (-1.47, 13352)]
    check("MLCC 조건충족 3종목",
          sum(ts.qualifies(r, c) for r, c in mlcc), 3)


def fake_list(pages):
    """가짜 목록 응답으로 fetch_theme_list 를 돌리고 (결과, 요청수) 를 돌려준다."""
    seen_urls = []

    def fake_fetch_json(url):
        seen_urls.append(url)
        page = int(url.split("page=")[1].split("&")[0])
        last = pages[max(pages)]
        return {"groups": pages.get(page, last)}   # 범위 초과 = 마지막 페이지 반복

    real_fetch, real_sleep = ts.fetch_json, ts.time.sleep
    ts.fetch_json, ts.time.sleep = fake_fetch_json, lambda _s: None
    try:
        return ts.fetch_theme_list(), len(seen_urls)
    finally:
        ts.fetch_json, ts.time.sleep = real_fetch, real_sleep


def listed(no, name, total=10, rise=5, rate="3.00"):
    """목록 API 가 주는 테마 한 줄."""
    return {"no": no, "name": name, "totalCount": total,
            "riseCount": rise, "changeRate": rate}


def test_theme_list_reads_every_page():
    """목록을 등락률 순으로 자르지 않는다.

    2026-09-11 실측: 조건을 충족한 테마 8개 중 4개가 등락률 100위 밖이었다
    (전기차 142위 -0.42%, 2차전지 214위 -1.10%). 테마 평균 등락률과 '그 안에
    급등주가 3개 있느냐'는 별개이고, 구성종목이 많은 테마일수록 평균이
    희석되므로 순위로 자르면 크고 무거운 테마가 통째로 잘린다.
    """
    pages = {1: [listed(1, "가"), listed(2, "나")],
             2: [listed(3, "다")], 3: [listed(4, "라")]}
    got, calls = fake_list(pages)
    check("세 페이지 다 읽음", [g["name"] for g in got], ["가", "나", "다", "라"])
    check("페이지 수만큼 부름", calls, ts.SCAN_PAGES)


def test_theme_list_stops_early_when_a_page_repeats():
    """같은 테마만 다시 오면 거기서 멈춘다 — 범위를 넘긴 것이다."""
    real = ts.SCAN_PAGES
    ts.SCAN_PAGES = 3
    try:
        got, calls = fake_list({1: [listed(1, "가")], 2: [listed(2, "나")]})
    finally:
        ts.SCAN_PAGES = real
    check("새 테마만 모음", [g["name"] for g in got], ["가", "나"])
    check("반복을 확인하는 한 번만 더", calls, 3)


def test_scan_counts_participation_from_the_stocks_themselves():
    """참여율은 구성종목의 등락률로 직접 센다.

    목록이 주는 riseCount 는 '조금이라도 오른' 종목이라 너무 관대하다 —
    2026-09-11 실측으로 266개 중 225개(85%)가 통과해 거르는 구실을 못 했다.
    원래 의도대로 PARTICIPATION_RATE 이상 오른 종목만 센다.
    """
    rows = [listed(1, "테마", total=99, rise=99)]   # 목록은 전부 올랐다고 말한다

    def fake_stocks(no):
        # 실제로는 다섯 중 셋만 3% 이상이다.
        rates = [9.0, 8.0, 7.0, 0.5, 0.2]
        return [{"name": f"종목{i}", "code": f"{i:06d}", "rate": r,
                 "cap": 5000, "amount": 50_000} for i, r in enumerate(rates)]

    real_list, real_stocks, real_sleep = (
        ts.fetch_theme_list, ts.fetch_theme_stocks, ts.time.sleep)
    ts.fetch_theme_list = lambda: rows
    ts.fetch_theme_stocks = fake_stocks
    ts.time.sleep = lambda _s: None
    try:
        out = ts.scan_themes()
    finally:
        ts.fetch_theme_list, ts.fetch_theme_stocks, ts.time.sleep = (
            real_list, real_stocks, real_sleep)
    check("목록의 99/99 를 믿지 않음",
          (out[0]["member_count"], out[0]["up_count"]), (5, 3))
    check("참여율 60%", ts.participation(out[0]), 0.6)


def test_scan_fetches_every_theme_it_listed():
    """목록 단계에서 미리 버리지 않는다 — 평균이 낮아도 상세를 봐야 안다."""
    rows = [listed(1, "평균낮음", total=40, rise=3), listed(2, "평균높음")]
    asked = []

    def fake_stocks(no):
        asked.append(no)
        return [{"name": f"종목{i}", "code": f"{i:06d}", "rate": 9.0,
                 "cap": 5000, "amount": 50_000} for i in range(3)]

    real_list, real_stocks, real_sleep = (
        ts.fetch_theme_list, ts.fetch_theme_stocks, ts.time.sleep)
    ts.fetch_theme_list = lambda: rows
    ts.fetch_theme_stocks = fake_stocks
    ts.time.sleep = lambda _s: None
    try:
        out = ts.scan_themes()
    finally:
        ts.fetch_theme_list, ts.fetch_theme_stocks, ts.time.sleep = (
            real_list, real_stocks, real_sleep)
    check("둘 다 상세 조회", asked, [1, 2])
    check("둘 다 결과에", [t["theme_name"] for t in out], ["평균낮음", "평균높음"])


def test_custom_theme_replaces_naver_theme():
    """직접 만든 'OLED' 가 네이버 'OLED(유기 발광 다이오드)' 를 대신한다.

    시세는 다른 네이버 테마에서 본 것을 쓴다.
    """
    rows = [listed(1, "OLED(유기 발광 다이오드)"), listed(2, "디스플레이 부품")]

    def fake_stocks(no):
        if no == 1:
            return [{"name": f"반도체{i}", "code": f"9{i:05d}", "rate": 9.0,
                     "cap": 5000, "amount": 50_000} for i in range(5)]
        return [{"name": n, "code": f"{i:06d}", "rate": 8.0, "cap": 5000,
                 "amount": 50_000} for i, n in enumerate(["덕산네오룩스", "야스", "AP시스템"])]

    real = (ts.fetch_theme_list, ts.fetch_theme_stocks, ts.time.sleep, ts.load_custom_themes)
    ts.fetch_theme_list = lambda: rows
    ts.fetch_theme_stocks = fake_stocks
    ts.time.sleep = lambda _s: None
    ts.load_custom_themes = lambda: [{"name": "OLED",
                                      "stocks": ["덕산네오룩스", "야스", "AP시스템", "없는종목"]}]
    try:
        out = ts.scan_themes()
    finally:
        ts.fetch_theme_list, ts.fetch_theme_stocks, ts.time.sleep, ts.load_custom_themes = real
    names = [t["theme_name"] for t in out]
    check("네이버 OLED 는 빠짐", "OLED(유기 발광 다이오드)" in names, False)
    custom = [t for t in out if t["theme_name"] == "OLED"]
    check("직접 테마가 들어옴", len(custom), 1)
    check("찾은 종목만 구성", custom[0]["member_count"], 3)
    check("조건 충족", len(custom[0]["qualifying"]), 3)


def test_numbers_come_out_of_naver_strings():
    """네이버는 숫자를 '15,130' 같은 문자열로 준다."""
    check("쉼표", ts.num("15,130"), 15130.0)
    check("소수", ts.num("29.98"), 29.98)
    check("부호", ts.num("+18,675"), 18675.0)
    check("없는 값", ts.num("N/A"), None)
    check("빈 값", ts.num(""), None)


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


def test_keyword_exclusion_removes_every_theme_under_it():
    """시트에 뜨는 건 키워드라, 키워드로 지목해도 그 아래 테마가 다 빠진다."""
    patterns = {"5G": ["5G(5세대 이동통신)", "통신장비"], "반도체": ["반도체 장비"]}
    ex = {"themes": [], "keywords": [{"keyword": "5G", "reason": "옛 테마"}],
          "stocks": []}
    kept = ts.filter_themes([
        theme("5G(5세대 이동통신)", [("가", 9.0), ("나", 8.0), ("다", 7.0)]),
        theme("통신장비", [("라", 9.0), ("마", 8.0), ("바", 7.0)]),
        theme("반도체 장비", [("사", 9.0), ("아", 8.0), ("자", 7.0)]),
    ], ex, patterns)
    check("키워드 아래가 전부 빠짐", [t["theme_name"] for t in kept], ["반도체 장비"])


def test_keyword_pattern_does_not_have_to_be_exact():
    """모델이 짓는 키워드는 날마다 표기가 흔들린다 — 부분 문자열로도 걸린다."""
    patterns = {"반도체 장비·소재": ["반도체 장비"]}
    ex = {"themes": [], "keywords": [{"keyword": "반도체 장비", "reason": "x"}],
          "stocks": []}
    kept = ts.filter_themes(
        [theme("반도체 장비", [("가", 9.0), ("나", 8.0), ("다", 7.0)])], ex, patterns)
    check("표기가 늘어나도 걸림", kept, [])


def test_stock_can_be_scoped_by_keyword():
    """범한퓨얼셀을 '조선기자재' 키워드에서만 뺀다 — 연료전지에서는 남는다."""
    patterns = {"조선기자재": ["조선기자재", "조선"], "연료전지": ["연료전지"]}
    ex = {"themes": [], "keywords": [], "stocks": [
        {"name": "범한퓨얼셀", "keywords": ["조선기자재"], "reason": "수소 쪽"}]}
    kept = ts.filter_themes([
        theme("조선기자재", [("범한퓨얼셀", 20.0), ("가", 9.0), ("나", 8.0), ("다", 7.0)]),
        theme("연료전지", [("범한퓨얼셀", 20.0), ("라", 9.0), ("마", 8.0)]),
    ], ex, patterns)
    names = {t["theme_name"]: [s["name"] for s in t["qualifying"]] for t in kept}
    check("키워드 범위에서 빠짐", names["조선기자재"], ["가", "나", "다"])
    check("다른 키워드에는 남음", names["연료전지"], ["범한퓨얼셀", "라", "마"])


def test_a_theme_with_no_keyword_is_not_swept_up():
    """어느 키워드에도 안 묶인 테마는 키워드 규칙에 걸리지 않는다.

    keyword_for 가 None 을 주는 경우다. 여기서 아무 규칙이나 걸리면 이름을
    모르는 새 테마가 통째로 사라진다.
    """
    ex = {"themes": [], "keywords": [{"keyword": "5G", "reason": "옛 테마"}],
          "stocks": []}
    kept = ts.filter_themes(
        [theme("처음보는테마", [("가", 9.0), ("나", 8.0), ("다", 7.0)])], ex, {})
    check("살아남음", [t["theme_name"] for t in kept], ["처음보는테마"])


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
          bool(ts.stock_exclusion(beomhan, "조선기자재", None, ex["stocks"])), True)
    check("범한퓨얼셀 @ 연료전지 안 걸림",
          ts.stock_exclusion(beomhan, "연료전지", None, ex["stocks"]), None)


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
