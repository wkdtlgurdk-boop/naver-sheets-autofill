#!/usr/bin/env python3
"""llm_group 의 제외 기록을 가짜 응답으로 검증한다.

여기 두 가지가 조용히 틀린다. 사람이 exclusions.json 에 적어둔 항목을 이
단계가 덮어쓰면 다음 날 제외가 풀리는데, 시트만 봐서는 왜 옛 테마가 다시
올라왔는지 알 수 없다. 반대로 모델이 크게 헛짚어 테마를 무더기로 지우면
그날이 아니라 그 뒤가 계속 얇아진다.

실행: python test_llm_group.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import llm_group as lg

failures: list[str] = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def theme(name, stocks):
    return {"theme_name": name, "theme_id": name,
            "qualifying": [{"name": n, "code": n, "rate": 9.0,
                            "cap": 5000, "amount": 100} for n in stocks]}


def with_file(doc):
    """doc 을 담은 임시 exclusions.json 을 붙였다 뗀다."""
    tmp = Path(tempfile.mkdtemp()) / "exclusions.json"
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return tmp


def run_record(doc, stale, misfits):
    tmp = with_file(doc)
    real = lg.EXCLUSIONS
    lg.EXCLUSIONS = tmp
    try:
        added = lg.record_exclusions(stale, misfits, "2026-09-10")
    finally:
        lg.EXCLUSIONS = real
    return added, json.loads(tmp.read_text(encoding="utf-8"))


def test_user_entries_survive_the_rewrite():
    """사람이 적은 제외는 남고, 새 항목은 뒤에 붙는다.

    keywords.json 은 매일 통째로 다시 쓰지만 이 파일은 그러면 안 된다 —
    '이 회사는 이제 통신 회사가 아니다' 는 하루짜리 사실이 아니다.
    """
    doc = {"_": "설명", "themes": [{"pattern": "일본 수출규제", "by": "user"}],
           "stocks": [{"name": "피노", "by": "user"}]}
    added, out = run_record(doc, [{"theme": "옛테마", "reason": "2018년"}], [])
    check("한 건 추가", added, 1)
    check("사람 항목 유지", out["themes"][0]["pattern"], "일본 수출규제")
    check("새 항목은 뒤에", out["themes"][1]["pattern"], "옛테마")
    check("출처 표시", out["themes"][1]["by"], "llm")
    check("종목 목록 유지", [s["name"] for s in out["stocks"]], ["피노"])
    check("설명 키 유지", out["_"], "설명")


def test_already_covered_theme_is_not_added_twice():
    """'5G' 가 이미 걸려 있으면 '5G(5세대 이동통신)' 을 또 적지 않는다."""
    doc = {"themes": [{"pattern": "5G", "by": "user"}], "stocks": []}
    added, out = run_record(doc, [{"theme": "5G(5세대 이동통신)", "reason": "옛날"}], [])
    check("추가 없음", added, 0)
    check("목록 그대로", len(out["themes"]), 1)


def test_misfit_stock_is_scoped_to_its_theme():
    """종목 제외는 지목한 테마에만 붙는다 — 전역으로 번지면 안 된다."""
    doc = {"themes": [], "stocks": []}
    added, out = run_record(doc, [], [{
        "stock": "범한퓨얼셀", "theme": "조선기자재", "belongs": "수소",
        "reason": "사업이 연료전지다"}])
    check("한 건 추가", added, 1)
    check("테마 한정", out["stocks"][0]["themes"], ["조선기자재"])
    check("belongs 가 이유에 남음", "수소" in out["stocks"][0]["reason"], True)


def test_same_stock_in_another_theme_is_a_separate_entry():
    doc = {"themes": [], "stocks": [
        {"name": "범한퓨얼셀", "themes": ["조선기자재"], "by": "llm"}]}
    added, out = run_record(doc, [], [{
        "stock": "범한퓨얼셀", "theme": "풍력", "belongs": "수소", "reason": "다르다"}])
    check("다른 테마는 새 항목", added, 1)
    check("두 항목", [s["themes"] for s in out["stocks"]],
          [["조선기자재"], ["풍력"]])


def test_unknown_names_are_ignored():
    """오늘 스캔에 없는 이름은 패턴이 되지 못한다.

    엉뚱한 문자열이 부분 문자열 패턴으로 들어가면 멀쩡한 테마를 잡아먹는다.
    """
    raw = [theme("반도체 장비", ["가", "나", "다"])]
    stale, misfits = lg.validate_exclusions(
        [{"theme": "없는테마", "reason": "x"}],
        [{"stock": "없는종목", "theme": "반도체 장비", "belongs": "", "reason": "x"},
         {"stock": "가", "theme": "없는테마", "belongs": "", "reason": "x"}],
        raw)
    check("테마 후보 버림", stale, [])
    check("종목 후보 버림", misfits, [])


def test_a_landslide_of_stale_proposals_is_thrown_away():
    """오늘 테마의 3분의 1을 넘게 지우자고 하면 판단이 아니라 오작동이다."""
    raw = [theme(f"t{i}", ["가", "나", "다"]) for i in range(6)]
    stale, _ = lg.validate_exclusions(
        [{"theme": f"t{i}", "reason": "옛날"} for i in range(4)], [], raw)
    check("전부 무시", stale, [])


def test_a_couple_of_stale_proposals_pass():
    raw = [theme(f"t{i}", ["가", "나", "다"]) for i in range(6)]
    stale, _ = lg.validate_exclusions(
        [{"theme": "t0", "reason": "옛날"}, {"theme": "t1", "reason": "옛날"}], [], raw)
    check("두 건 통과", [s["theme"] for s in stale], ["t0", "t1"])


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
