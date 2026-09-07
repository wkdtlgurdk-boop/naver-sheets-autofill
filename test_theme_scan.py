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


def theme(name, stocks):
    return {"theme_name": name, "theme_id": name,
            "qualifying": [stock(n, r) for n, r in stocks]}


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


def test_exact_theme_name_beats_a_shorter_substring():
    """'LED장비' 가 'LED' 패턴에 먹히지 않는다.

    keywords.json 은 정확한 테마명을 담고, 한쪽이 다른 쪽의 접두사인 경우가
    흔하다. 부분문자열만 보면 먼저 검사된 키워드가 긴 이름을 가져가 버린다.
    """
    patterns = {"대표주": ["LED"], "장비": ["LED장비"]}
    check("정확 일치 우선", ts.keyword_for("LED장비", patterns), "장비")
    check("정확 일치 자기 자신", ts.keyword_for("LED", patterns), "대표주")


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
