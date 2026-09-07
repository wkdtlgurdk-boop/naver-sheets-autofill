#!/usr/bin/env python3
"""오늘 스캔한 테마들을 Claude 에게 묶게 하고 keywords.json 을 갱신한다.

기계적 병합(merge_by_overlap)은 '오늘 조건을 충족한 종목'이 겹치는지만 본다.
그 집합은 날마다 흔들려서, 같은 산업의 두 테마가 서로 다른 종목이 올랐다는
이유로 갈라진다(9/7: 유리 기판과 플렉서블 디스플레이가 1위 종목이 같은데도
따로 남았다). 반대로 크게 뭉친 그룹은 '제일 짧은 이름'을 라벨로 달아서,
반도체 8개 테마가 합쳐진 그룹이 'LED' 라는 간판을 달고 나왔다.

두 문제 다 이름과 산업 지식이 필요한 판단이라 규칙으로는 한계가 있다.
그래서 이 단계만 모델에게 맡기고, 결과는 사람이 읽고 고칠 수 있는
keywords.json 으로 남긴다 — 다음 실행에 그대로 반영된다.

안전 원칙: 이 단계는 절대 파이프라인을 죽이지 않는다. 키가 없거나 호출이
실패하거나 응답이 검증을 통과하지 못하면, 기존 keywords.json 을 그대로 두고
정상 종료한다. 그러면 기계적 병합이 어제까지의 키워드로 돌아간다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MODEL = "claude-opus-5"
HERE = Path(__file__).parent
RAW = HERE / "raw_scan.json"
KEYWORDS = HERE / "keywords.json"

# 시트의 하루 블록이 가진 슬롯 수. 그룹이 이보다 많으면 뒤쪽이 잘린다.
TARGET_GROUPS = 8

SYSTEM = """\
당신은 한국 주식 시장의 테마를 정리합니다.

네이버 증권의 테마는 서로 겹칩니다. 같은 산업을 다른 이름으로 부르거나
(예: '반도체 장비'와 'LED장비'), 한 종목이 여러 테마에 동시에 속합니다.
이걸 그대로 두면 일일 시트의 슬롯을 중복된 테마가 차지해서, 정작 다른
산업의 움직임이 밀려납니다.

오늘 조건을 충족한 테마 목록과 각 테마에서 오른 종목을 드립니다.
이것을 겹치지 않는 그룹으로 묶으세요.

규칙:
- 각 테마는 정확히 한 그룹에만 들어갑니다. 어느 그룹에도 안 넣어도 됩니다.
- 같은 산업·같은 공급망이거나 종목이 많이 겹치면 묶으세요.
- 그룹 이름(keyword)은 그 그룹을 가장 잘 대표하는 한국어 이름으로 직접
  지으세요. 묶인 테마 중 하나를 골라도 되고 새로 지어도 됩니다. 짧다는
  이유로 고르지 마세요 — 반도체 8개 테마를 묶은 그룹에 'LED'라는 이름이
  붙은 적이 있는데, 그룹의 내용을 오해하게 만듭니다.
- 그룹은 8개 이하가 되도록 묶으세요. 그보다 많으면 시트에서 잘립니다.
- 종목이 겹치지 않고 산업도 다르면 억지로 묶지 마세요. 서로 다른 테마를
  합치면 그룹의 평균상승률이 의미를 잃습니다.
- reason 은 왜 묶었는지 한 문장. 사람이 이 판단을 검토할 때 읽습니다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "source_themes": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["keyword", "source_themes", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["groups"],
    "additionalProperties": False,
}


def give_up(why: str) -> int:
    """기존 keywords.json 을 그대로 두고 정상 종료."""
    print(f"[llm_group] 건너뜀 — {why}", file=sys.stderr)
    print("[llm_group] 기존 keywords.json 을 그대로 사용합니다.", file=sys.stderr)
    return 0


def describe(raw: list[dict]) -> str:
    lines = []
    for t in raw:
        stocks = ", ".join(
            f"{s['name']}({s['rate']:+.1f}%)" for s in t["qualifying"]
        )
        lines.append(f"- {t['theme_name']}: {stocks}")
    return "\n".join(lines)


def validate(groups: list[dict], known: set[str]) -> list[dict]:
    """스캔에 없는 테마명과 중복 배정을 걸러낸다.

    모델이 테마 이름을 살짝 바꿔 적으면 apply_keyword_overrides 가 아무것도
    못 찾고 조용히 무시한다 — 실패가 눈에 안 보인다. 여기서 걸러 로그에 남긴다.
    """
    seen: dict[str, str] = {}
    out = []
    for g in groups:
        kw = (g.get("keyword") or "").strip()
        if not kw:
            continue
        themes = []
        for name in g.get("source_themes", []):
            name = name.strip()
            if name not in known:
                print(f"[llm_group] 무시: '{name}' 는 오늘 스캔에 없는 테마입니다.",
                      file=sys.stderr)
                continue
            if name in seen:
                print(f"[llm_group] 무시: '{name}' 가 '{seen[name]}' 와 "
                      f"'{kw}' 양쪽에 있습니다.", file=sys.stderr)
                continue
            seen[name] = kw
            themes.append(name)
        if themes:
            out.append({"keyword": kw, "source_themes": themes,
                        "reason": (g.get("reason") or "").strip()})
    return out


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return give_up("ANTHROPIC_API_KEY 가 없습니다")
    if not RAW.exists():
        return give_up(f"{RAW.name} 이 없습니다 (theme_scan.py 를 먼저 실행)")

    raw = json.loads(RAW.read_text(encoding="utf-8"))
    if not raw:
        return give_up("오늘 조건을 충족한 테마가 0개입니다")
    known = {t["theme_name"] for t in raw}

    try:
        import anthropic
    except ImportError:
        return give_up("anthropic 패키지가 설치되지 않았습니다")

    prompt = (
        f"오늘 조건을 충족한 테마 {len(raw)}개입니다. "
        f"슬롯은 {TARGET_GROUPS}개뿐이니 그 안에 들어가도록 묶어 주세요.\n\n"
        + describe(raw)
    )

    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"},
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except Exception as exc:  # noqa: BLE001 - 어떤 실패든 파이프라인은 계속된다
        return give_up(f"API 호출 실패: {type(exc).__name__}: {exc}")

    if resp.stop_reason == "refusal":
        return give_up(f"모델이 거부했습니다 ({resp.stop_details})")

    try:
        text = next(b.text for b in resp.content if b.type == "text")
        groups = validate(json.loads(text)["groups"], known)
    except (StopIteration, json.JSONDecodeError, KeyError, TypeError) as exc:
        return give_up(f"응답을 읽지 못했습니다: {type(exc).__name__}: {exc}")

    if not groups:
        return give_up("검증을 통과한 그룹이 없습니다")

    before = KEYWORDS.read_text(encoding="utf-8") if KEYWORDS.exists() else ""
    payload = json.dumps(groups, ensure_ascii=False, indent=2) + "\n"
    KEYWORDS.write_text(payload, encoding="utf-8")

    print(f"[llm_group] {len(raw)}개 테마 -> {len(groups)}개 그룹", file=sys.stderr)
    for g in groups:
        print(f"  [{g['keyword']}] {', '.join(g['source_themes'])}", file=sys.stderr)
        print(f"      {g['reason']}", file=sys.stderr)
    leftover = known - {t for g in groups for t in g["source_themes"]}
    if leftover:
        print(f"  (어느 그룹에도 안 들어감: {', '.join(sorted(leftover))})",
              file=sys.stderr)
    print(f"[llm_group] keywords.json {'변경됨' if payload != before else '동일'}",
          file=sys.stderr)
    print(f"[llm_group] usage: in={resp.usage.input_tokens} "
          f"out={resp.usage.output_tokens}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
