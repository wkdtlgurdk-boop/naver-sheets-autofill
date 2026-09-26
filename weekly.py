#!/usr/bin/env python3
"""한 주 결산 카드. 금요일 밤에 그 주 Daily 블록을 모아 두 장을 그리고 스레드에
캐러셀로 올린다.

    python weekly.py cards          # 카드만 (cards/<금요일>_weekly/)
    python weekly.py post cards     # 올리기 (theme_bot 에 카드가 먼저 올라가 있어야 한다)

카드 두 장:
  01 이번 주 테마   그 주에 뜬 테마를 며칠 나왔는지 순으로. 주간 수익률은 그
                    테마 종목들의 '지난주 마지막 종가 -> 이번 주 마지막 종가' 평균.
  02 이번 주 주도주 시트에 한 번이라도 오른 종목 중 주간 수익률 상위.

같은 테마인지는 theme_stats.same_theme 으로 가른다(이름이 같거나 종목이 둘 이상
겹침). 이름은 모델이 날마다 조금씩 다르게 붙인다.

하루 카드와 같은 틀(cards.Card)을 쓴다. 색·글꼴·여백이 같아야 캐러셀을 넘길 때
다른 계정 글처럼 보이지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import theme_stats as st

KST = timezone(timedelta(hours=9))
MAX_THEMES = 9
MAX_STOCKS = 9
ORDINAL = ["첫째", "둘째", "셋째", "넷째", "다섯째"]
BLUE = (90, 150, 255)


# ---------------------------------------------------------------- 날짜

def week_of(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=4)


def week_label(friday: date) -> str:
    """'9월 넷째 주'. 금요일이 그 달 몇 번째 주에 드는지로 센다."""
    return f"{friday.month}월 {ORDINAL[min((friday.day - 1) // 7, 4)]} 주"


# ---------------------------------------------------------------- 집계

def cluster_week(days: dict[date, list[dict]]) -> list[dict]:
    """한 주의 그룹들을 같은 테마끼리 묶는다. 날짜 순으로 훑으며 앞 묶음에 붙인다."""
    clusters: list[dict] = []
    for day in sorted(days):
        for g in days[day]:
            home = next((c for c in clusters
                         if day not in c["days"] and st.same_theme(c["last"], g)), None)
            if home is None:
                home = {"days": [], "names": [], "stocks": {}, "last": g}
                clusters.append(home)
            home["days"].append(day)
            home["names"].append(g["keyword"])
            home["last"] = g
            for s in g["stocks"]:
                prev = home["stocks"].get(s["name"])
                home["stocks"][s["name"]] = max(prev or -999.0, s["rate"])
    for c in clusters:
        counts = Counter(c["names"])
        # 가장 많이 쓴 이름, 같으면 마지막 이름.
        c["keyword"] = max(reversed(c["names"]), key=lambda n: counts[n])
    return clusters


def week_return(bars: list[dict], monday: date, friday: date) -> float | None:
    """지난주 마지막 종가 -> 이번 주 마지막 종가(%). 이번 주 봉이 없으면 None."""
    before = [b for b in bars if b["date"] < monday]
    inside = [b for b in bars if monday <= b["date"] <= friday]
    if not before or not inside:
        return None
    return (inside[-1]["close"] / before[-1]["close"] - 1) * 100


def summarize_week(days: dict[date, list[dict]], bars_by_code: dict[str, list[dict]],
                   code_map: dict[str, str], monday: date, friday: date) -> dict:
    ret: dict[str, float] = {}
    for gs in days.values():
        for g in gs:
            for s in g["stocks"]:
                code = code_map.get(s["name"])
                if code and s["name"] not in ret:
                    r = week_return(bars_by_code.get(code, []), monday, friday)
                    if r is not None:
                        ret[s["name"]] = r

    themes = []
    for c in cluster_week(days):
        vals = [ret[n] for n in c["stocks"] if n in ret]
        leader = max(c["stocks"].items(), key=lambda kv: kv[1])[0]
        themes.append({"keyword": c["keyword"], "days": len(c["days"]),
                       "dates": [d.isoformat() for d in c["days"]],
                       "week_return": round(sum(vals) / len(vals), 2) if vals else None,
                       "leader": leader, "stocks": sorted(c["stocks"])})
    themes.sort(key=lambda t: (-t["days"], -(t["week_return"] if t["week_return"] is not None else -999)))

    seen = Counter(s["name"] for gs in days.values() for g in gs for s in g["stocks"])
    theme_of = {}
    for t in themes:
        for n in t["stocks"]:
            theme_of.setdefault(n, t["keyword"])
    stocks = [{"name": n, "week_return": round(r, 2), "days": seen[n], "theme": theme_of.get(n, "")}
              for n, r in ret.items()]
    stocks.sort(key=lambda s: -s["week_return"])
    return {"monday": monday.isoformat(), "friday": friday.isoformat(),
            "trading_days": [d.isoformat() for d in sorted(days)],
            "themes": themes, "stocks": stocks}


# ---------------------------------------------------------------- 카드

def _rate_color(v: float | None):
    import cards
    return cards.RED if (v or 0) >= 0 else BLUE


def _rate(v: float | None) -> str:
    return "—" if v is None else f"{v:+.2f}%"


def theme_card(summary: dict, when: datetime):
    import cards
    c = cards.Card(when, "WEEKLY")
    days = summary["trading_days"]
    span = f"{days[0][5:].replace('-', '/')} ~ {days[-1][5:].replace('-', '/')}" if days else ""
    c.title("이번 주 테마", f"{span} · {len(days)}거래일 · 나온 날 순")
    c.d.text((cards.M, 405), "테마", font=cards.font("medium", 24), fill=cards.MUTED, anchor="ls")
    c.d.text((700, 405), "나온 날", font=cards.font("medium", 24), fill=cards.MUTED, anchor="rs")
    c.d.text((1008, 405), "주간 수익률", font=cards.font("medium", 24), fill=cards.MUTED, anchor="rs")
    c.d.rectangle([cards.M, 425, cards.R, 426], fill=cards.RULE_S)
    y = 492
    rows = summary["themes"][:MAX_THEMES]
    for i, t in enumerate(rows):
        top = i == 0
        c.d.text((cards.M, y), str(i + 1), font=cards.font("medium", 28),
                 fill=cards.GOLD if top else cards.MUTED, anchor="ls")
        name = cards.fit(c.d, t["keyword"], cards.font("bold", 34), 520)
        c.d.text((128, y), name, font=cards.font("bold", 34),
                 fill=cards.GOLD if top else cards.INK, anchor="ls")
        c.d.text((700, y), f"{t['days']}일", font=cards.font("black", 32),
                 fill=cards.GOLD if t["days"] >= 3 else cards.SOFT, anchor="rs")
        c.d.text((1008, y), _rate(t["week_return"]), font=cards.font("black", 34),
                 fill=_rate_color(t["week_return"]), anchor="rs")
        if i < len(rows) - 1:
            c.row_rule(y + 26)
        y += 80
    return c


def stock_card(summary: dict, when: datetime):
    import cards
    c = cards.Card(when, "WEEKLY")
    c.title("이번 주 주도주", "시트에 오른 종목 · 주간 수익률 순")
    c.d.text((cards.M, 405), "종목 · 테마", font=cards.font("medium", 24), fill=cards.MUTED, anchor="ls")
    c.d.text((1008, 405), "주간 수익률", font=cards.font("medium", 24), fill=cards.MUTED, anchor="rs")
    c.d.rectangle([cards.M, 425, cards.R, 426], fill=cards.RULE_S)
    y = 492
    rows = summary["stocks"][:MAX_STOCKS]
    for i, s in enumerate(rows):
        top = i == 0
        c.d.text((cards.M, y), s["name"], font=cards.font("bold", 34),
                 fill=cards.GOLD if top else cards.INK, anchor="ls")
        w = c.d.textlength(s["name"], font=cards.font("bold", 34))
        sub = cards.fit(c.d, f"  {s['theme']} · {s['days']}일", cards.font("medium", 24),
                        700 - int(w))
        c.d.text((cards.M + w, y), sub, font=cards.font("medium", 24), fill=cards.MUTED, anchor="ls")
        c.d.text((1008, y), _rate(s["week_return"]), font=cards.font("black", 34),
                 fill=_rate_color(s["week_return"]), anchor="rs")
        if i < len(rows) - 1:
            c.row_rule(y + 26)
        y += 80
    return c


def folder(friday: date) -> str:
    return f"{friday:%Y%m%d}_weekly"


def render(summary: dict, out_dir: Path, friday: date) -> list[Path]:
    when = datetime(friday.year, friday.month, friday.day, 20, 0, tzinfo=KST)
    target = out_dir / folder(friday)
    return [theme_card(summary, when).save(target / "01_themes.png"),
            stock_card(summary, when).save(target / "02_stocks.png")]


# ---------------------------------------------------------------- 게시

def caption(summary: dict, friday: date) -> str:
    """첫 줄에 'YYYY년 M월 D일' 을 쓰지 않는다. post_threads.already_posted 가 그
    꼴로 하루 글을 찾으므로, 여기 금요일 날짜를 쓰면 늦게 뜬 금요일 하루 실행이
    이 글을 자기 글로 알고 게시를 건너뛴다."""
    lines = [f"멍! {friday.year}년 {week_label(friday)} 테마 결산 물어왔개 🐕", "", "이번 주 테마"]
    for i, t in enumerate(summary["themes"][:MAX_THEMES], 1):
        lines.append(f"{i}. {t['keyword']} {t['days']}일 {_rate(t['week_return'])}")
    lines += ["", "한 주 수고했개 🐾"]
    text = "\n".join(lines)
    return text if len(text) <= 500 else "\n".join(lines[:-2])[:500]


def post(paths: list[Path], summary: dict, friday: date) -> int:
    import post_threads as pt
    user_id = os.environ.get("THREADS_USER_ID", "").strip()
    token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
    if not user_id or not token:
        return pt.give_up("THREADS_USER_ID / THREADS_ACCESS_TOKEN 이 없습니다")

    head = caption(summary, friday).split("\n", 1)[0]
    try:
        doc = pt.get(f"{user_id}/threads", {"fields": "id,text", "limit": 25, "access_token": token})
        for p in doc.get("data") or []:
            if (p.get("text") or "").split("\n", 1)[0] == head:
                return pt.give_up(f"이번 주 결산이 이미 올라가 있습니다 ({p.get('id')})")
    except Exception as exc:  # noqa: BLE001 - 확인 실패로 게시를 막지 않는다
        print(f"[threads] 기존 글 확인 실패 — 그대로 올립니다 ({exc})", file=sys.stderr)

    try:
        children = []
        for p in paths:
            got = pt.call(f"{user_id}/threads", {
                "media_type": "IMAGE", "image_url": pt.raw_url(p, folder(friday)),
                "is_carousel_item": "true", "access_token": token})
            children.append(got["id"])
        for cid in children:
            pt.wait_ready(cid, token)
        parent = pt.call(f"{user_id}/threads", {
            "media_type": "CAROUSEL", "children": ",".join(children),
            "text": caption(summary, friday), "access_token": token})
        time.sleep(pt.SETTLE_SECONDS)
        posted = pt.call(f"{user_id}/threads_publish",
                         {"creation_id": parent["id"], "access_token": token})
    except (RuntimeError, KeyError, urllib.error.URLError) as exc:
        return pt.give_up(f"{type(exc).__name__}: {exc}")
    print(f"[threads] 주간 결산 게시 — {posted.get('id')} (카드 {len(children)}장)", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- 실행

def target_friday() -> date:
    """THEME_AS_OF 가 있으면 그 주, 없으면 이번 주."""
    raw = os.environ.get("THEME_AS_OF", "").strip()
    day = date.fromisoformat(raw) if raw else datetime.now(KST).date()
    return week_of(day)[1]


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("cards", "post"):
        print("쓰기: weekly.py cards [출력폴더] | weekly.py post [출력폴더]", file=sys.stderr)
        return 2
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("cards")
    friday = target_friday()
    monday = friday - timedelta(days=4)
    summary_path = out_dir / folder(friday) / "summary.json"

    if sys.argv[1] == "post":
        paths = sorted((out_dir / folder(friday)).glob("*.png"))
        if not paths or not summary_path.exists():
            print(f"[중단] {out_dir / folder(friday)} 에 카드가 없습니다.", file=sys.stderr)
            return 1
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return post(paths, summary, friday)

    from feed_export import load_code_map
    from sheets import Spreadsheet

    book = Spreadsheet(st.DAILY_ID)
    days: dict[date, list[dict]] = {}
    for title in book.titles():
        ym = st.tab_year_month(title)
        if ym is None or ym not in {(monday.year, monday.month), (friday.year, friday.month)}:
            continue
        found = st.days_from_grid(book.read_formatted(book.gid_for_title(title),
                                                      f"A1:AN{st.SEARCH_ROWS}"), *ym)
        for d, gs in found.items():
            if monday <= d <= friday and len(gs) >= len(days.get(d, [])):
                days[d] = gs
    if not days:
        print(f"[중단] {monday}~{friday} 블록이 시트에 없습니다.", file=sys.stderr)
        return 1
    print(f"{monday} ~ {friday}: 시트 {len(days)}일")

    code_map = load_code_map()
    names = {s["name"] for gs in days.values() for g in gs for s in g["stocks"]}
    bars = {c: st.fetch_bars(c, monday - timedelta(days=10), friday)
            for c in sorted({code_map[n] for n in names if n in code_map})}
    trading = sorted({b["date"] for bs in bars.values() for b in bs})
    closed = sorted(set(days) - set(st.only_trading(days, trading)))
    if closed:
        print(f"  장이 안 선 날의 블록은 뺍니다: {', '.join(map(str, closed))}")
    days = st.only_trading(days, trading)
    summary = summarize_week(days, bars, code_map, monday, friday)
    for t in summary["themes"]:
        print(f"  [{t['keyword']}] {t['days']}일 {_rate(t['week_return'])} (대장 {t['leader']})")

    made = render(summary, out_dir, friday)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    for p in made:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
