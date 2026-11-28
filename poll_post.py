#!/usr/bin/env python3
"""스레드에 투표 글 한 건을 올린다 (손으로 한 번 돌리는 용도).

스레드 API 의 투표는 텍스트 글에만 붙는다(이미지·캐러셀과 못 섞는다).
컨테이너를 만들 때 poll_attachment 에 option_a..option_d JSON 을 주고,
그 컨테이너를 publish 한다. 선택지는 2~4개, 각 1~25자.

사용:
    python poll_post.py --dry-run            # 올리지 않고 내용만 출력
    python poll_post.py                      # 기본 문구로 게시
    python poll_post.py --text "..." --option "A" --option "B"

안전 원칙: 같은 본문 글이 최근에 이미 있으면 올리지 않는다. 토큰이 없으면
실패로 끝낸다 (손으로 누른 실행이라 조용히 넘어가면 안 된다).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import post_threads as pt

DEFAULT_TEXT = (
    "멍멍! 주트리버가 물어보개 🐕\n"
    "수급·시황 정리 다음엔 어떤 콘텐츠를 물어다 줄까개?\n"
    "\n"
    "투표로 정하개 👇 (다 같이 골라주면 신나개 🐾)"
)
DEFAULT_OPTIONS = [
    "수급·시황 핵심 종목",
    "핵심 테마 분석",
    "차트 분석법",
    "실적·공시 정리",
]
MIN_OPTIONS, MAX_OPTIONS, OPTION_LIMIT = 2, 4, 25


def poll_attachment(options: list[str]) -> str:
    """API 가 받는 poll_attachment JSON. 규칙을 어기면 ValueError."""
    options = [o.strip() for o in options]
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise ValueError(f"선택지는 {MIN_OPTIONS}~{MAX_OPTIONS}개여야 합니다 ({len(options)}개)")
    for o in options:
        if not 1 <= len(o) <= OPTION_LIMIT:
            raise ValueError(f"선택지는 1~{OPTION_LIMIT}자여야 합니다: {o!r} ({len(o)}자)")
    return json.dumps({f"option_{chr(97 + i)}": o for i, o in enumerate(options)},
                      ensure_ascii=False)


def already_posted(user_id: str, token: str, text: str) -> str | None:
    doc = pt.get(f"{user_id}/threads",
                 {"fields": "id,text", "limit": 25, "access_token": token})
    head = text.split("\n", 1)[0]
    for post in doc.get("data") or []:
        if (post.get("text") or "").split("\n", 1)[0] == head:
            return post.get("id")
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=DEFAULT_TEXT)
    ap.add_argument("--option", action="append", dest="options")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    options = args.options or DEFAULT_OPTIONS
    if len(args.text) > pt.TEXT_LIMIT:
        print(f"본문이 {pt.TEXT_LIMIT}자를 넘습니다 ({len(args.text)}자)", file=sys.stderr)
        return 1
    attach = poll_attachment(options)

    print(args.text)
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    if args.dry_run:
        print("[poll] dry-run — 올리지 않았습니다.")
        return 0

    user_id = os.environ.get("THREADS_USER_ID")
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not user_id or not token:
        print("THREADS_USER_ID / THREADS_ACCESS_TOKEN 이 없습니다", file=sys.stderr)
        return 1
    dup = already_posted(user_id, token, args.text)
    if dup:
        print(f"[poll] 같은 첫 줄의 글이 이미 있습니다 ({dup}) — 올리지 않습니다.",
              file=sys.stderr)
        return 0
    got = pt.call(f"{user_id}/threads", {
        "media_type": "TEXT", "text": args.text,
        "poll_attachment": attach, "access_token": token})
    time.sleep(pt.SETTLE_SECONDS)
    pid = pt.call(f"{user_id}/threads_publish", {
        "creation_id": got["id"], "access_token": token}).get("id")
    print(f"[poll] 게시 완료 — {pid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
