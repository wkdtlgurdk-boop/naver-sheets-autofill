#!/usr/bin/env python3
"""종목이 오늘 그 테마로 움직였다고 볼 수 있는가 -- 1년치 시트 기록으로 판단한다.

## 왜

네이버 테마의 구성종목은 넓고 낡는다. 삼성전자는 '자율주행차' 테마에도 들어
있어서, 반도체로 오른 날(2026-09-21 +5.36%) 자율주행차 칸의 대장주가 됐다.
SK하이닉스가 '로봇' 칸에(9/18), 대한광통신이 '반도체·IT 대표주' 칸에(9/16)
앉은 것도 같은 모양이다. exclusions.json 에 한 줄씩 적어 막아 왔지만 사람이
보고 나서야 막힌다.

시트에는 이미 답이 있다. 삼성전자는 1년 동안 시트에 35번 올랐고 그중 25번이
'반도체', 자율주행차는 한 번이다. **평소에 한 번도 그 계열로 움직인 적이 없는
종목이 오늘 그 칸의 대장주가 되면, 테마가 아니라 제 이유로 오른 것이다.**

## 규칙

오늘 그룹 G 에 종목 S 를 넣으려 할 때,
    S 의 과거 기록이 MIN_HISTORY 번 이상이고
    그중 G 와 같은 계열(이름·원본 테마명의 낱말이 겹치거나 같은 FAMILY)이
    MAX_SHARE 미만이면
→ S 를 G 에서 뺀다. S 는 다른 그룹(제 계열)이 가져갈 수 있다.

기록이 적은 종목(신규 상장, 처음 뜬 종목)은 판단하지 않는다. 모르는 것을
틀렸다고 하지 않는다.

계열(FAMILIES)을 두는 이유: '전력설비' 칸의 한전기술(원자력 14번)이나
'원자력발전' 칸의 대한전선(전선 10번)은 낱말은 달라도 같은 흐름이다. 그런
이웃 산업을 오배정으로 치지 않으려고 묶어 둔다. 틀린 묶음이 보이면 여기를
고치면 된다.

기록(affinity.json)은 theme_stats.py 가 금요일마다 시트 전체에서 다시 만든다.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

AFFINITY_FILE = Path(__file__).parent / "affinity.json"

MIN_HISTORY = 6     # 이만큼은 시트에 올랐어야 판단한다
MAX_SHARE = 0.15    # 같은 계열로 오른 비율이 이보다 낮으면 오배정

# 계열을 가르지 못하는 낱말. '반도체 소재'와 '2차전지 소재'를 '소재'로 잇지 않는다.
STOP = {"관련", "관련주", "대표주", "테마", "소재", "장비", "부품", "소부장", "생산",
        "수혜", "기타", "및", "등", "it", "ai", "국산화", "日", "수출규제", "수출",
        "규제", "실적", "주요종목", "산업", "기업", "대장주", "기술"}

# 같은 흐름으로 보는 이웃 산업. 낱말 하나라도 이 목록의 말을 품으면 그 계열이다.
FAMILIES = {
    "반도체": ["반도체", "삼전닉스", "hbm", "socamm", "소캠", "cxl", "idm", "pcb", "기판",
             "mlcc", "온디바이스", "칩", "파운드리", "메모리", "d램", "낸드", "후공정",
             "전공정", "유리기판", "tc본더", "뉴로모픽"],
    "전력": ["원자력", "원전", "smr", "전력", "전선", "변압기", "전력설비", "전력기기",
           "송전", "배전", "초전도"],
    "2차전지": ["2차전지", "이차전지", "배터리", "리튬", "양극재", "음극재", "전고체",
              "폐배터리", "전해질", "분리막", "ess"],
    "조선": ["조선", "조선기자재", "lng", "해운", "피팅", "밸브", "hrsg"],
    "우주": ["우주", "우주항공", "스페이스x", "space", "spacex", "위성", "항공", "uam", "드론"],
    "방산": ["방산", "방위산업", "전쟁", "테러", "재건", "전후"],
    "수소": ["수소", "연료전지", "sofc"],
    "자동차": ["자동차", "자율주행", "전기차", "완성차", "타이어"],
    "로봇": ["로봇", "휴머노이드", "감속기", "협동로봇"],
    "통신": ["광통신", "통신", "통신장비", "5g", "6g", "양자", "양자암호", "양자컴퓨팅", "보안"],
    "바이오": ["바이오", "제약", "신약", "치료제", "비만", "알츠하이머", "항암", "백신",
             "의료", "의료기기", "헬스케어", "cmo", "cdmo", "진단"],
    "뷰티": ["화장품", "미용", "뷰티", "k-뷰티"],
    "금융": ["증권", "은행", "보험", "지주사", "밸류업", "금융", "스테이블코인", "가상자산",
           "두나무", "코인", "결제", "핀테크", "sto"],
    "건설": ["건설", "건자재", "시멘트", "재건"],
    "디스플레이": ["디스플레이", "oled", "led", "폰", "스마트폰", "카메라모듈"],
}
ALIAS = {"이차전지": "2차전지"}


def tokens(text: str) -> set[str]:
    out = set()
    for w in re.split(r"[·/()\[\]\s,&+\-]+", str(text or "")):
        w = ALIAS.get(w.strip().lower(), w.strip().lower())
        if len(w) >= 2 and w not in STOP:
            out.add(w)
    return out


def families(words: set[str]) -> set[str]:
    out = set()
    for w in words:
        for fam, marks in FAMILIES.items():
            if any(m.lower() in w for m in marks):
                out.add(fam)
    return out


def related(a: set[str], b: set[str]) -> bool:
    """낱말이 같거나 한쪽이 다른 쪽을 품거나('자율주행' ⊂ '자율주행차'), 같은 계열."""
    for x in a:
        for y in b:
            if x == y or x in y or y in x:
                return True
    return bool(families(a) & families(b))


def fit_share(history: dict[str, int], names: list[str]) -> tuple[int, int]:
    """(과거 기록 수, 그중 names 와 같은 계열인 수)."""
    target = set().union(*(tokens(n) for n in names)) if names else set()
    total = sum(history.values())
    same = sum(c for kw, c in history.items() if related(tokens(kw), target))
    return total, same


def misfit(name: str, names: list[str], affinity: dict[str, dict[str, int]]) -> str | None:
    """오배정이면 사유 한 줄, 아니면 None."""
    history = affinity.get(name) or {}
    total, same = fit_share(history, names)
    if total < MIN_HISTORY or same / total >= MAX_SHARE:
        return None
    home = ", ".join(f"{k} {v}번" for k, v in Counter(history).most_common(2))
    return f"과거 {total}번 중 이 계열 {same}번 (주로 {home})"


def build_affinity(days: dict) -> dict[str, dict[str, int]]:
    """{종목명: {키워드: 횟수}}. days 는 theme_stats.read_sheet 의 결과."""
    out: dict[str, Counter] = {}
    for groups in days.values():
        for g in groups:
            for s in g["stocks"]:
                out.setdefault(s["name"], Counter())[g["keyword"]] += 1
    return {n: dict(c.most_common()) for n, c in sorted(out.items())}


def load_affinity(path: Path | None = None) -> dict[str, dict[str, int]]:
    path = path or AFFINITY_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
