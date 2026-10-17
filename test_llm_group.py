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


def run_preserve(exclusions, old_keywords, groups):
    """제외 파일과 어제의 keywords.json 을 붙여 놓고 보존 로직을 돌린다."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / "exclusions.json").write_text(
        json.dumps(exclusions, ensure_ascii=False), encoding="utf-8")
    (tmp / "keywords.json").write_text(
        json.dumps(old_keywords, ensure_ascii=False), encoding="utf-8")
    real_ex, real_kw = lg.EXCLUSIONS, lg.KEYWORDS
    lg.EXCLUSIONS, lg.KEYWORDS = tmp / "exclusions.json", tmp / "keywords.json"
    try:
        lg.preserve_excluded_keywords(groups)
    finally:
        lg.EXCLUSIONS, lg.KEYWORDS = real_ex, real_kw
    return groups


def test_excluded_keyword_keeps_its_theme_map():
    """제외된 키워드가 새 지도에서 사라지면 다음 실행에 제외가 풀린다.

    키워드 제외는 keywords.json 의 키워드 -> 테마 지도를 타고 걸린다. 그런데
    그렇게 빠진 테마는 모델에게 보이지도 않으니 모델의 새 지도에는 그 키워드가
    없다. 그대로 덮어쓰면 --regroup 단계에서 지도가 끊겨 어제 뺀 테마가 오늘
    슬롯에 돌아온다.
    """
    groups = [{"keyword": "반도체", "source_themes": ["반도체 장비"], "reason": ""}]
    out = run_preserve(
        {"keywords": [{"keyword": "5G", "reason": "옛 테마"}]},
        [{"keyword": "5G", "source_themes": ["5G(5세대 이동통신)", "통신장비"]}],
        groups)
    check("지도가 남음", [g["keyword"] for g in out], ["반도체", "5G"])
    check("테마도 그대로", out[1]["source_themes"], ["5G(5세대 이동통신)", "통신장비"])


def test_keyword_named_only_by_a_stock_rule_is_kept_too():
    """종목 제외의 keywords 범위도 같은 지도를 쓴다."""
    groups = [{"keyword": "반도체", "source_themes": ["반도체 장비"], "reason": ""}]
    out = run_preserve(
        {"stocks": [{"name": "범한퓨얼셀", "keywords": ["조선기자재"], "reason": "수소"}]},
        [{"keyword": "조선기자재", "source_themes": ["조선기자재"]}],
        groups)
    check("지도가 남음", [g["keyword"] for g in out], ["반도체", "조선기자재"])


def test_a_theme_the_model_claimed_is_not_preserved_twice():
    """모델이 오늘 가져간 테마는 옛 항목에서 뺀다 — 한 테마는 한 키워드에만."""
    groups = [{"keyword": "통신장비", "source_themes": ["통신장비"], "reason": ""}]
    out = run_preserve(
        {"keywords": [{"keyword": "5G", "reason": "옛 테마"}]},
        [{"keyword": "5G", "source_themes": ["5G(5세대 이동통신)", "통신장비"]}],
        groups)
    check("중복 없음", out[1]["source_themes"], ["5G(5세대 이동통신)"])


def test_nothing_is_preserved_without_keyword_rules():
    groups = [{"keyword": "반도체", "source_themes": ["반도체 장비"], "reason": ""}]
    out = run_preserve(
        {"themes": [{"pattern": "5G", "reason": "옛 테마"}]},
        [{"keyword": "5G", "source_themes": ["5G(5세대 이동통신)"]}],
        groups)
    check("테마명 규칙은 지도가 필요 없음", [g["keyword"] for g in out], ["반도체"])


def test_사용자_묶기_규칙():
    g = lambda kw, *t: {"keyword": kw, "source_themes": list(t), "reason": "r"}
    out = lg.enforce_user_rules([
        g("전력기기·ESS", "전력저장장치(ESS)"),
        g("바이오", "바이오시밀러(복제 바이오의약품)"),
        g("제약·바이오 신약", "제약업체", "면역항암제"),
        g("반도체 밸류체인(HBM·장비·시스템반도체)", "HBM(고대역폭메모리)", "시스템반도체"),
        g("반도체 소재·부품", "반도체 소재·부품"),
        g("생체인식", "바이오인식(생체인식)"),
    ])
    names = [x["keyword"] for x in out]
    assert names == ["ESS", "바이오", "반도체(HBM·시스템반도체)", "반도체 소재·부품", "생체인식"], names
    assert out[1]["source_themes"] == ["바이오시밀러(복제 바이오의약품)", "제약업체", "면역항암제"], out[1]


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


def test_성공_경로가_끝까지_돈다():
    """main() 을 성공 경로로 통째로 태운다.

    이것이 없어서 llm_group.py 가 2026-09-21 에 터졌다. main() 맨 끝에 SDK
    시절의 `resp.usage` 가 남아 있었는데, 실패하면 give_up() 으로 먼저 빠져
    나가므로 그 줄에 닿지 않았다. CLI 호출이 사흘 만에 처음 성공한 날 비로소
    NameError 가 났고, 그룹핑은 다 끝난 뒤라 keywords.json 은 갱신됐는데
    종료코드 1 이 워크플로를 넘어뜨려 카드와 피드가 통째로 안 나갔다.

    즉 '잘 됐을 때만 터지는' 버그였다. 실패 경로만 시험하면 영영 안 잡힌다.
    """
    d = Path(tempfile.mkdtemp())
    raw = d / "raw_scan.json"
    raw.write_text(json.dumps([
        theme("반도체 장비", ["가", "나", "다"]),
        theme("로봇", ["라", "마", "바"]),
    ], ensure_ascii=False), encoding="utf-8")

    answer = {
        "groups": [
            {"keyword": "반도체", "source_themes": ["반도체 장비"],
             "reason": "장비 한 갈래"},
            {"keyword": "로봇", "source_themes": ["로봇"], "reason": "단독"},
        ],
        "stale_themes": [],
        "misfit_stocks": [],
    }

    saved = (lg.RAW, lg.KEYWORDS, lg.EXCLUSIONS, lg.cli_available, lg.run_claude)
    try:
        lg.RAW = raw
        lg.KEYWORDS = d / "keywords.json"
        lg.KEYWORDS.write_text("[]\n", encoding="utf-8")
        lg.EXCLUSIONS = with_file({"themes": [], "stocks": []})
        lg.cli_available = lambda: None
        lg.run_claude = lambda prompt: json.dumps(answer, ensure_ascii=False)
        code = lg.main()
    finally:
        (lg.RAW, lg.KEYWORDS, lg.EXCLUSIONS,
         lg.cli_available, lg.run_claude) = saved
        written = json.loads((d / "keywords.json").read_text(encoding="utf-8"))

    check("종료코드", code, 0)
    check("그룹 수", len(written), 2)


def test_usage_가_없어도_죽지_않는다():
    """봉투에 usage 가 없어도 호출은 성공으로 끝나야 한다.

    사용량 출력은 관측용이다. 그 한 줄 때문에 그룹핑 결과를 통째로 버리는
    일이 다시 없어야 한다.
    """
    import subprocess as sp

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = json.dumps({"subtype": "success", "result": "본문"})

    real_run = sp.run
    try:
        sp.run = lambda *a, **k: FakeProc()
        got = lg.run_claude("프롬프트")
    finally:
        sp.run = real_run
    check("usage 없어도 본문을 준다", got, "본문")



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
