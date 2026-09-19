#!/usr/bin/env python3
"""구독 토큰이 살아 있는지만 본다. 15초짜리 진단.

    python token_check.py

## 왜 따로 두는가

CLAUDE_CODE_OAUTH_TOKEN 이 거부되면 테마 묶기는 규칙 기반으로 조용히
물러나고, 시트도 카드도 스레드도 평소처럼 채워진다. 보는 사람 눈에는 묶음이
조금 거칠어질 뿐이다 -- 2026-09-18 부터 사흘을 그렇게 보냈다. 전체 실행으로
토큰을 시험하려면 네이버 스캔 3분 반을 거쳐 시트와 스레드까지 건드려야
하는데, 토큰을 새로 넣은 사람은 그 전에 알고 싶다.

그래서 네이버 스캔도 시트도 거치지 않고 CLI 만 한 번 두드린다. 실패하면
종료코드 1 이라 워크플로가 빨갛게 뜬다 -- 본 작업과 달리 여기서는 물러설
곳이 없어야 한다.

## 값을 찍지 않는다

토큰은 길이와 앞 몇 글자만 본다. 401 의 흔한 원인이 따옴표째 붙여넣기,
중간에 잘림, 앞뒤 공백이라 그 셋은 값을 보지 않고도 잡힌다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from llm_group import MODEL, explain

ENV = "CLAUDE_CODE_OAUTH_TOKEN"
EXPECTED_PREFIX = "sk-ant-oat01-"
TIMEOUT_SEC = 120


def shape(raw: str) -> tuple[list[str], bool]:
    """(지적할 점, 그래도 호출해 볼 만한가). 값을 드러내지 않고 모양만 본다.

    401 의 흔한 원인 셋 -- 따옴표째 붙여넣기, 중간에 잘림, 앞뒤 공백 -- 은
    값을 보지 않고도 잡힌다. 그중 잘림과 API 키는 두드려 볼 것도 없어서
    두 번째 값을 False 로 돌려준다. 확실히 틀린 값으로 2분을 기다릴 이유가
    없다.
    """
    problems, worth_trying = [], True
    if raw != raw.strip():
        problems.append("앞이나 뒤에 공백·줄바꿈이 붙어 있습니다")
    value = raw.strip()
    if value[:1] in ('"', "'") or value[-1:] in ('"', "'"):
        problems.append("따옴표가 값에 포함돼 있습니다")
        # 따옴표를 벗겨야 그 안의 접두어가 보인다. 안 벗기면 모든 값이
        # "sk-ant-oat01- 로 시작하지 않습니다" 로만 나와 진짜 문제를 덮는다.
        value = value.strip("\"'")
    if value.startswith("sk-ant-api"):
        problems.append("이것은 API 키입니다 -- 구독이 아니라 크레딧으로 과금됩니다. "
                        "`claude setup-token` 으로 만든 값이 필요합니다")
        worth_trying = False
    elif not value.startswith(EXPECTED_PREFIX):
        problems.append(f"{EXPECTED_PREFIX} 로 시작하지 않습니다")
    if len(value) < 60:
        problems.append(f"{len(value)}자뿐입니다 -- 복사하다 잘린 것 같습니다")
        worth_trying = False
    return problems, worth_trying


def main() -> int:
    if shutil.which("claude") is None:
        print("claude CLI 가 없습니다. npm install -g @anthropic-ai/claude-code")
        return 1

    raw = os.environ.get(ENV, "")
    if not raw.strip():
        print(f"{ENV} 이 비어 있습니다.")
        return 1

    value = raw.strip()
    print(f"{ENV}: {len(value)}자, {value[:len(EXPECTED_PREFIX)]}… 로 시작")
    problems, worth_trying = shape(raw)
    for problem in problems:
        print(f"  ※ {problem}")
    if not worth_trying:
        print("호출해 보지 않고 멈춥니다 -- 위 내용이 그대로 거부 사유입니다.")
        return 1

    if os.environ.get("ANTHROPIC_API_KEY"):
        # 인증 우선순위상 API 키가 이긴다. 이게 켜져 있으면 아래 호출이
        # 성공해도 구독을 쓴 것이 아니다.
        print("  ※ ANTHROPIC_API_KEY 가 같이 설정돼 있습니다 -- 그쪽이 이깁니다")

    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", MODEL,
        "--max-turns", "1",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--restricted",
    ]
    try:
        proc = subprocess.run(cmd, input="1+1은?", capture_output=True,
                              text=True, timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        print(f"{TIMEOUT_SEC}초 안에 답이 없습니다.")
        return 1

    if proc.returncode != 0:
        print(f"거부됐습니다 (종료코드 {proc.returncode}): "
              f"{explain(proc.stderr, proc.stdout)}")
        return 1

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"응답을 읽지 못했습니다: {proc.stdout[:200]}")
        return 1

    if envelope.get("is_error"):
        print(f"거부됐습니다: {explain('', proc.stdout)}")
        return 1

    print(f"토큰 정상 — 모델 {MODEL}, 응답 {str(envelope.get('result', ''))[:60]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
