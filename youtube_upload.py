#!/usr/bin/env python3
"""shorts.py 가 만든 그날 영상을 유튜브 쇼츠로 올린다.

인증은 OAuth 리프레시 토큰 하나로 한다. 사람이 한 번 브라우저로 허락해 받은
토큰을 시크릿에 넣어 두면, 매 실행 여기서 액세스 토큰으로 바꿔 쓴다(받는 법은
README 「유튜브 쇼츠」). 라이브러리를 더 들이지 않으려고 urllib 로 직접 부른다.

  YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN   셋 다 있어야 올린다
  YT_PRIVACY          public(기본) · unlisted · private
  YT_ALLOW_BACKFILL   THEME_AS_OF 로 지난 날짜를 다시 만든 실행에서도 올릴 때 1

post_threads.py 와 같은 원칙을 따른다.
  - 시크릿이 없거나 호출이 실패해도 파이프라인을 죽이지 않는다(카드·스레드는 이미 나갔다).
  - 같은 날 두 번 올리지 않는다. 예약 실행이 하루 세 번까지 뜨기 때문이다.
    따로 상태 파일을 두지 않고, 내 채널 업로드 목록의 제목에서 날짜를 찾는다.
  - 지난 날짜 실행은 기본적으로 올리지 않는다.

주의: Google 이 감사(audit)하지 않은 API 프로젝트로 올린 영상은 공개로 요청해도
**비공개로 잠긴다**(2020-07 이후 만든 프로젝트). 그 경우 로그에 그렇게 남기고,
유튜브 스튜디오에서 손으로 공개로 바꾸거나 감사를 신청해야 한다.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import cards
from as_of import as_of

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
TITLE_LIMIT = 100
DESC_LIMIT = 5000
# 태그는 검색·추천에 거의 영향이 없고, 많이 넣으면 메타데이터 남용으로 보일 수
# 있다(2026-10-06 사용자 결정: 고정 5개 + 그날 상위 테마 4개).
BASE_TAGS = ["주식", "테마주", "오늘의테마", "장마감", "특징주"]
TOP_THEMES = 4


def give_up(why: str) -> int:
    print(f"[youtube] 업로드 건너뜀 — {why}", file=sys.stderr)
    return 0


def request(url: str, *, data: bytes | None = None, headers: dict | None = None,
            method: str | None = None, want_headers: bool = False):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read().decode() or "{}"
            return (json.loads(body), resp.headers) if want_headers else json.loads(body)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code} {url.split('?')[0]}: {e.read().decode(errors='ignore')[:400]}") from None


def access_token(client_id: str, secret: str, refresh: str) -> str:
    body = urllib.parse.urlencode({"client_id": client_id, "client_secret": secret,
                                   "refresh_token": refresh, "grant_type": "refresh_token"}).encode()
    return request(TOKEN_URL, data=body,
                   headers={"Content-Type": "application/x-www-form-urlencoded"})["access_token"]


def date_mark(now: datetime) -> str:
    """제목 앞머리이자 중복 확인 표식. '10월 2일' 만으로는 해가 바뀌면 겹치므로 연도까지."""
    return f"{now:%Y.%m.%d}"


def title(groups: list[dict], now: datetime) -> str:
    top = cards.ranked(groups)[0]["keyword"] if groups else ""
    t = f"{date_mark(now)} 오늘의 테마 TOP {len(groups)} | 1위 {top} #shorts"
    return t if len(t) <= TITLE_LIMIT else f"{date_mark(now)} 오늘의 테마 TOP {len(groups)} #shorts"


def tags(groups: list[dict]) -> list[str]:
    """고정 태그 + 그날 상위 테마 TOP_THEMES 개의 이름(괄호 앞)."""
    out = list(BASE_TAGS)
    for g in cards.ranked(groups):
        name = g["keyword"].split("(")[0].strip()
        if name and name not in out:
            out.append(name)
        if len(out) >= len(BASE_TAGS) + TOP_THEMES:
            break
    return out


def description(groups: list[dict], now: datetime) -> str:
    lines = [f"{now.year}년 {now.month}월 {now.day}일 장 마감 테마 정리 (거래대금 순 · 평균상승률)", ""]
    for i, g in enumerate(cards.ranked(groups), 1):
        lines.append(f"{i}. {g['keyword']} {cards.avg_rate(g['top5']):+.2f}%")
    lines += ["", cards.footer_text(), "투자 권유가 아닌 시세 정리입니다.", "",
              " ".join(["#shorts #오늘의테마 #테마주"]
                       + [f"#{g['keyword'].split('(')[0].replace(' ', '').replace('·', '')}"
                          for g in cards.ranked(groups)[:1]])]
    return "\n".join(lines)[:DESC_LIMIT]


def already_uploaded(token: str, now: datetime) -> str | None:
    """내 채널 최근 업로드 50개 중 제목이 그날 표식으로 시작하는 영상의 id.

    확인에 실패하면 막지 않는다 — 한 번 더 올라가는 것보다 그날 영상이 통째로
    빠지는 쪽이 나쁘다(post_threads.already_posted 와 같은 판단).
    """
    auth = {"Authorization": f"Bearer {token}"}
    try:
        ch = request(f"{API}/channels?part=contentDetails&mine=true", headers=auth)
        uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        items = request(f"{API}/playlistItems?part=snippet&maxResults=50&playlistId={uploads}",
                        headers=auth).get("items") or []
    except Exception as exc:  # noqa: BLE001
        print(f"[youtube] 기존 업로드 확인 실패 — 그대로 올립니다 ({exc})", file=sys.stderr)
        return None
    mark = date_mark(now)
    for it in items:
        sn = it.get("snippet") or {}
        if (sn.get("title") or "").startswith(mark):
            return (sn.get("resourceId") or {}).get("videoId")
    return None


def upload(token: str, path: Path, meta: dict) -> dict:
    size = path.stat().st_size
    _, hdrs = request(f"{UPLOAD}?uploadType=resumable&part=snippet,status",
                      data=json.dumps(meta).encode(), method="POST", want_headers=True,
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json; charset=UTF-8",
                               "X-Upload-Content-Type": "video/mp4",
                               "X-Upload-Content-Length": str(size)})
    session = hdrs["Location"]
    return request(session, data=path.read_bytes(), method="PUT",
                   headers={"Content-Type": "video/mp4", "Content-Length": str(size)})


def main() -> int:
    cid, secret, refresh = (os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))
    if not (cid and secret and refresh):
        return give_up("YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN 이 없습니다")

    here = Path(__file__).parent
    now, overridden = as_of()
    backfill_ok = os.environ.get("YT_ALLOW_BACKFILL") == "1"
    if overridden and not backfill_ok:
        print(f"THEME_AS_OF={now:%Y-%m-%d} — 지난 날짜 실행이라 올리지 않습니다. "
              "올리려면 YT_ALLOW_BACKFILL=1 을 주세요.")
        return 0

    video = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "shorts" / f"{now:%Y%m%d}.mp4"
    if not video.exists():
        return give_up(f"{video} 이 없습니다 (shorts.py 가 영상을 만들지 못한 날)")
    groups = json.loads((here / "final_groups.json").read_text(encoding="utf-8"))

    privacy = os.environ.get("YT_PRIVACY", "public")
    try:
        token = access_token(cid, secret, refresh)
        done = already_uploaded(token, now)
        if done and not backfill_ok:
            return give_up(f"{date_mark(now)} 영상이 이미 올라가 있습니다 (https://youtu.be/{done})")
        meta = {"snippet": {"title": title(groups, now), "description": description(groups, now),
                            "tags": tags(groups),
                            "categoryId": CATEGORY, "defaultLanguage": "ko"},
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
        got = upload(token, video, meta)
    except Exception as exc:  # noqa: BLE001
        return give_up(str(exc))

    vid = got.get("id")
    status = (got.get("status") or {}).get("privacyStatus")
    print(f"[youtube] 올림 https://youtube.com/shorts/{vid} ({status})")
    if privacy == "public" and status != "public":
        print("[youtube] 공개로 요청했지만 '%s' 로 올라갔습니다. 감사받지 않은 API 프로젝트의 "
              "업로드는 비공개로 잠깁니다 — 유튜브 스튜디오에서 공개로 바꾸거나, "
              "Google 의 YouTube API 감사를 신청하세요." % status, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
