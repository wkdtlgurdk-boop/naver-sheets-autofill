"""이 실행이 '어느 날'을 대상으로 하는지 한 곳에서 정한다.

같은 답이 필요한 곳이 넷이다 — 시트 블록을 찾는 theme_write, 카드에 찍히는
날짜와 폴더 이름을 만드는 cards, 스레드에 올릴 이미지 URL 을 만드는
post_threads, 그리고 카드를 공개 리포에 올리는 워크플로 단계. 각자
datetime.now() 를 부르던 탓에 2026-09-11 을 다시 채우는 실행이 시트는 9/11 에
쓰고 카드는 9/13(일) 로 찍어 스레드에까지 올렸다. 날짜를 셋이 따로 계산하면
언젠가 반드시 갈라진다.

THEME_AS_OF 가 있으면 그 날을 '오늘'로 본다. 지난 날짜를 지목했다는 것은
주말 가드도 건너뛰겠다는 뜻이다 — 장이 닫힌 날 지난 날짜를 손보는 것이 바로
이 기능의 용도다.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def as_of() -> tuple[datetime, bool]:
    """(대상 날짜, 사람이 지목했는지).

    두 번째 값이 True 면 지난 날짜를 손보는 실행이다. 부르는 쪽은 그걸 보고
    평소와 다르게 굴 수 있다 — 주말 가드를 건너뛰거나, 이미 지난 날짜를
    스레드에 다시 올리지 않거나.
    """
    raw = os.environ.get("THEME_AS_OF", "").strip()
    if not raw:
        return datetime.now(KST), False
    try:
        day = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise SystemExit(f"[중단] THEME_AS_OF 는 YYYY-MM-DD 형식이어야 합니다: {raw!r}")
    return day.replace(tzinfo=KST), True
