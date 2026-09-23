#!/usr/bin/env python3
"""오늘 카드를 스레드(Threads)에 캐러셀 한 건으로 올린다.

스레드 API 는 이미지를 업로드받지 않는다. 공개된 URL 을 주면 메타 서버가
그 URL 을 가져간다. 그래서 카드는 공개 리포(theme_bot)에 먼저 올라가고,
여기서는 그 raw URL 을 넘긴다 — theme_bot 이 public 인 이유가 이것이다.

절차는 두 단계다. 각 이미지를 '컨테이너'로 만들고(is_carousel_item), 그
아이디들을 children 으로 묶어 캐러셀 컨테이너를 만든 뒤, 마지막에 publish
한다. 컨테이너는 만들자마자 게시하면 이미지를 아직 못 받아온 상태라
실패할 수 있어서, 메타 문서 권고대로 사이에 시간을 둔다.

안전 원칙: 토큰이 없거나 호출이 실패해도 파이프라인을 죽이지 않는다.
카드는 이미 리포에 올라가 있으므로, 게시만 건너뛰고 정상 종료한다 —
스레드 토큰(60일)이 만료된 날 시트까지 빨갛게 만들 이유가 없다.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cards
from as_of import as_of

KST = timezone(timedelta(hours=9))
API = "https://graph.threads.net/v1.0"
CARD_REPO = os.environ.get("CARD_REPO", "wkdtlgurdk-boop/theme_bot")
CARD_BRANCH = os.environ.get("CARD_BRANCH", "main")

# 컨테이너를 만든 뒤 게시까지 두는 시간(초). 메타 문서가 권하는 값이다.
SETTLE_SECONDS = int(os.environ.get("THREADS_SETTLE_SECONDS", "10"))
READY_TRIES = 20          # 한 장당 최대 100초까지 기다린다
READY_INTERVAL = 5
MAX_ITEMS = 20        # 캐러셀 상한
TEXT_LIMIT = 500      # 본문 글자수 상한


def give_up(why: str) -> int:
    print(f"[threads] 게시 건너뜀 — {why}", file=sys.stderr)
    print("[threads] 카드는 리포에 올라가 있습니다.", file=sys.stderr)
    return 0


def get(path: str, params: dict) -> dict:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{API}/{path}?{query}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:300]
        raise RuntimeError(f"{e.code} {path}: {body}") from None


def wait_ready(container_id: str, token: str) -> None:
    """메타가 이미지를 다 받아올 때까지 기다린다.

    컨테이너를 만드는 요청은 즉시 아이디를 돌려주지만, 그 시점에 메타는 아직
    우리 URL 에서 이미지를 가져오는 중이다(status=IN_PROGRESS). 그 상태의
    아이디를 캐러셀 children 으로 넘기면 'invalid, nonexistent, or expired'
    로 거부된다 — 2026-09-11 첫 게시 시도가 정확히 그렇게 죽었다. 마지막에
    올린 장이 늘 제일 안 익어 있으므로 순서상 그 장이 걸린다.

    그래서 캐러셀을 만들기 전에 각 장이 FINISHED 가 되기를 기다린다.
    """
    for _ in range(READY_TRIES):
        got = get(container_id, {"fields": "status,error_message",
                                 "access_token": token})
        status = got.get("status")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(
                f"컨테이너 {container_id} 가 {status}: {got.get('error_message')}")
        time.sleep(READY_INTERVAL)
    raise RuntimeError(f"컨테이너 {container_id} 가 "
                       f"{READY_TRIES * READY_INTERVAL}초 안에 준비되지 않았습니다")


def call(path: str, params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{API}/{path}", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:400]
        raise RuntimeError(f"{e.code} {path}: {body}") from None


def already_posted(user_id: str, token: str, now: datetime) -> str | None:
    """오늘 글이 이미 올라가 있으면 그 글의 id. 없으면 None.

    본문 첫 줄이 '멍! 2026년 9월 18일 시황정리...' 라 날짜가 그대로 표식이
    된다. 따로 상태 파일을 두지 않는 이유다 -- 스레드에 올라간 글 자체가
    가장 믿을 만한 기록이고, 러너가 바뀌어도 캐시가 지워져도 남아 있다.

    확인에 실패하면 막지 않는다. 한 번 더 올라가는 것보다 그날 글이 통째로
    빠지는 쪽이 나쁘다 -- 다만 조용히 넘어가지는 않는다.
    """
    try:
        doc = get(f"{user_id}/threads",
                  {"fields": "id,text", "limit": 25, "access_token": token})
    except Exception as exc:
        print(f"[threads] 기존 글 확인 실패 — 그대로 올립니다 ({exc})", file=sys.stderr)
        return None
    mark = caption_date(now)
    for post in doc.get("data") or []:
        first = (post.get("text") or "").split("\n", 1)[0]
        if mark in first:
            return post.get("id")
    return None


def raw_url(path: Path, day: str) -> str:
    return (f"https://raw.githubusercontent.com/{CARD_REPO}/{CARD_BRANCH}"
            f"/cards/{day}/{urllib.parse.quote(path.name)}")


def caption_date(now: datetime) -> str:
    """'2026년 9월 11일'. 카드 위의 '2026.09.11 (금)' 과 달리 본문은 풀어 쓴다."""
    return f"{now.year}년 {now.month}월 {now.day}일"


def caption(groups: list[dict], now: datetime) -> str:
    """본문. 카드를 못 여는 사람도 순위는 읽을 수 있게 텍스트로 남긴다.

    해시태그는 달지 않는다. 스레드는 인스타처럼 태그를 여러 개 붙이는 곳이
    아니라서 '#테마주 #주식 #급등주' 같은 줄이 광고처럼 읽힌다.

    글머리에 '시황정리' 를 쓰는 이유: 캐러셀 첫 장이 지수·거래대금·외인
    요약이고 그 뒤가 테마별 카드라, 글 전체를 묶는 말로는 테마보다 넓다.
    """
    lines = [f"멍! {caption_date(now)} 시황정리 물어왔개 🐕", "", "오늘의 테마"]
    for i, g in enumerate(cards.ranked(groups), 1):
        lines.append(f"{i}. {g['keyword']} {cards.avg_rate(g['top5']):+.2f}%")
    lines += ["", "오늘도 수고했개 🐾"]

    text = "\n".join(lines)
    if len(text) <= TEXT_LIMIT:
        return text
    # 테마가 많은 날은 꼬리 문구부터 버린다. 순위가 잘리는 것보다 낫다.
    return "\n".join(lines[:-2])[:TEXT_LIMIT]


def main() -> int:
    user_id = os.environ.get("THREADS_USER_ID")
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not user_id or not token:
        return give_up("THREADS_USER_ID / THREADS_ACCESS_TOKEN 이 없습니다")

    here = Path(__file__).parent
    # 본문 날짜와 카드 폴더는 '오늘'이 아니라 이 글이 다루는 거래일을 따른다.
    # cards.py 가 같은 출처로 폴더를 만들었으므로 둘이 어긋나지 않는다.
    now, overridden = as_of()
    if overridden and os.environ.get("THREADS_ALLOW_BACKFILL") != "1":
        # 그 날짜 글은 이미 그날 올라갔다. 다시 올리면 같은 날 글이 둘이 된다.
        # 지난 날짜 실행은 시트와 카드를 고치러 도는 것이므로 게시는 막는다.
        print(f"THEME_AS_OF={now:%Y-%m-%d} — 지난 날짜 실행이라 게시하지 않습니다. "
              "올리려면 THREADS_ALLOW_BACKFILL=1 을 주세요.")
        return 0
    if overridden:
        print(f"[threads] THEME_AS_OF={now:%Y-%m-%d} 기준으로 올립니다.", file=sys.stderr)
    # 같은 날 두 번 올리지 않는다. 시계가 둘이기 때문이다 -- Apps Script 가
    # 20:10 에 띄우고, 그게 안 뜬 날을 위해 GitHub cron 이 21:10 에 한 번 더
    # 돈다. 둘 다 뜬 날에는 뒤엣것이 여기서 멈춘다.
    posted_id = already_posted(user_id, token, now)
    if posted_id and os.environ.get("THREADS_ALLOW_BACKFILL") == "1":
        # 사람이 repost 로 부른 실행이다. 그 입력은 설명부터 "같은 날 글이 둘이
        # 될 수 있음" 이라고 말하고 있으므로, 여기서 막으면 그 입력이 하는 일이
        # 없어진다. 9/22 를 중복 카드 없이 다시 올리려던 실행이 이 줄에서 1초
        # 만에 멈췄다 -- 리포의 카드만 갈아 끼우고 스레드 글은 옛것 그대로였다.
        # 메타는 게시 때 이미지를 받아다 자기 쪽에 두므로, 리포를 고쳐도 이미
        # 올라간 글은 바뀌지 않는다. 다시 올리는 것이 유일한 방법이다.
        print(f"[threads] {caption_date(now)} 글이 이미 있지만({posted_id}) "
              "다시 올립니다 — 같은 날 글이 둘이 됩니다.", file=sys.stderr)
    elif posted_id:
        return give_up(f"{caption_date(now)} 글이 이미 올라가 있습니다 ({posted_id})")

    day = f"{now:%Y%m%d}"
    card_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "cards"
    paths = sorted((card_dir / day).glob("*.png"))
    if not paths:
        return give_up(f"{card_dir / day} 에 카드가 없습니다")
    if len(paths) > MAX_ITEMS:
        print(f"[threads] 카드 {len(paths)}장 중 앞 {MAX_ITEMS}장만 올립니다.",
              file=sys.stderr)
        paths = paths[:MAX_ITEMS]

    groups = json.loads((here / "final_groups.json").read_text(encoding="utf-8"))

    try:
        children = []
        for p in paths:
            url = raw_url(p, day)
            got = call(f"{user_id}/threads", {
                "media_type": "IMAGE", "image_url": url,
                "is_carousel_item": "true", "access_token": token})
            children.append(got["id"])
            print(f"  올림 {p.name} -> {got['id']}", file=sys.stderr)

        # 묶기 전에 전부 익기를 기다린다. 여기를 건너뛰면 마지막 장이 아직
        # IN_PROGRESS 라 캐러셀이 통째로 거부된다.
        for cid in children:
            wait_ready(cid, token)
        print(f"  {len(children)}장 준비 완료", file=sys.stderr)

        text = caption(groups, now)
        parent = call(f"{user_id}/threads", {
            "media_type": "CAROUSEL", "children": ",".join(children),
            "text": text, "access_token": token})

        # 캐러셀 컨테이너 자체도 잠깐 자리를 잡는다.
        time.sleep(SETTLE_SECONDS)
        posted = call(f"{user_id}/threads_publish", {
            "creation_id": parent["id"], "access_token": token})
    except (RuntimeError, KeyError, urllib.error.URLError) as exc:
        return give_up(f"{type(exc).__name__}: {exc}")

    print(f"[threads] 게시 완료 — {posted.get('id')} (카드 {len(children)}장)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
