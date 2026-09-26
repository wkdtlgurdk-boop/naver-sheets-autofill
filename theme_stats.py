#!/usr/bin/env python3
"""테마 지속성 통계. Daily Keyword Calendar 전체를 되짚어 '뜬 테마가 다음 날도
가는가, 그 대장주를 다음 날 시가에 사면 어땠는가'를 센다.

    python theme_stats.py --out stats_out            # 시트 + 네이버 일봉
    python theme_stats.py --out stats_out --days 180 # 최근 180일만

## 무엇을 세는가

시트의 한 블록(하루)에는 그룹이 여러 개 있고, 그룹마다 종목이 상승률 순으로
적혀 있다. 그룹 하나를 '테마-일' 하나로 본다.

- **이어짐**: 다음 거래일 블록에 같은 테마가 또 있는가. 키워드 이름은 모델이
  그날그날 붙여서 흔들리므로(9/21 '반도체 기판·AI PCB', 9/22 '반도체 기판·PCB')
  이름이 같거나 **종목이 둘 이상 겹치면** 같은 테마로 본다.
- **N일차**: 며칠째 이어진 테마인가. 시트에 빠진 날이 있으면 거기서 끊는다
  -- 모르는 날을 '이어졌다'로 칠 수 없다.
- **수익률**: 진입은 **다음 거래일 시가**다. 아침 알림을 보고 살 수 있는 첫
  가격이 그것이다(tradesignal/prognosis.py 와 같은 기준). 기준일 종가 대비
  밤사이 갭은 따로 적는다.

      gap  = 시가(D+1) / 종가(D) - 1
      r1   = 종가(D+1) / 시가(D+1) - 1
      r3   = 종가(D+3) / 시가(D+1) - 1
      r5   = 종가(D+5) / 시가(D+1) - 1

  대장주(그 블록에서 상승률 1위)와 그룹 평균을 따로 낸다.

## 결과

`<out>/latest.json` 과 `<out>/report.md`. 봇은 JSON 의 `current` 를 읽어
오늘 테마가 몇 일차인지, 과거 같은 일차의 성적이 어땠는지를 알림에 붙인다.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from groups_from_sheet import HEADER_MARK, _cell, block_date, header_rows, parse_rate

KST = timezone(timedelta(hours=9))
DAILY_ID = "1xq_WNQh04xV1UsLm9ZRL6HhkQIJn-34G41zm87oR2h4"
SEARCH_ROWS = 700
MAX_STOCKS = 15
OVERLAP = 2          # 종목이 이만큼 겹치면 같은 테마
HORIZONS = (1, 3, 5)
LIMIT_UP = 29.5      # 상한가로 보는 상승률

DAY_CHART_URL = "https://api.stock.naver.com/chart/domestic/item/{code}/day"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://stock.naver.com/",
    "User-Agent": "Mozilla/5.0 theme-stats/1.0",
}


# ---------------------------------------------------------------- 시트 읽기

def tab_year_month(title: str) -> tuple[int, int] | None:
    """탭 이름 -> (연, 월). 달력 탭이 아니면 None.

    이름 규칙이 세 번 바뀌었다.
        '26.9월'              지금 규칙 (theme_write.daily_tab_title)
        '2026- 1월' '2025-6월' 해가 바뀌는 달에 사람이 붙인 이름
        '12월' ... '2월'       연도 없는 옛 탭. 2025-7월 ~ 2026-6월이다
    연도 없는 탭은 7~12월이면 2025, 1~6월이면 2026 으로 읽는다. '26.M월' 규칙이
    2026-7월에 시작됐으므로 연도 없는 탭이 그 뒤에 생길 일은 없다.
    """
    t = title.replace(" ", "")
    m = re.fullmatch(r"(\d{2})\.(\d{1,2})월", t)
    if m:
        return 2000 + int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"(\d{4})-(\d{1,2})월", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"(\d{1,2})월", t)
    if m:
        month = int(m.group(1))
        return (2025 if month >= 7 else 2026), month
    return None


def parse_block_day(text: str, tab_year: int, tab_month: int) -> date | None:
    """A열 값 'M/D' -> 날짜. 9월 탭 첫 블록이 8/31 인 것처럼 앞뒤 달이 섞인다."""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", str(text).strip())
    if not m:
        m2 = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(text).strip())
        if not m2:
            return None
        return date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
    month, day = int(m.group(1)), int(m.group(2))
    year = tab_year
    if tab_month == 1 and month == 12:
        year -= 1
    elif tab_month == 12 and month == 1:
        year += 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def clean_name(text: str) -> str:
    """옛 탭에는 손으로 적은 이름 끝에 '\xa0*' 같은 표시가 붙어 있다('셀리드 *')."""
    return re.sub(r"[\s*]+$", "", str(text).replace("\xa0", " ")).strip()


def days_from_grid(grid: list[list], tab_year: int, tab_month: int) -> dict[date, list[dict]]:
    """탭 하나 -> {날짜: [그룹...]}. 그룹은 {"keyword", "stocks": [{"name","rate"}]}.

    groups_from_sheet.groups_from_grid 와 같은 자리를 읽지만 거래대금을 요구하지
    않는다. 2025년 탭에는 거래대금 열이 빈 블록이 있고, 여기서 필요한 것은
    이름과 상승률뿐이다.
    """
    out: dict[date, list[dict]] = {}
    for header in header_rows(grid):
        day = parse_block_day(block_date(grid, header), tab_year, tab_month)
        # 달이 탭과 두 달 이상 어긋난 블록은 버린다. 6월 탭 한가운데에 '2/11'
        # 이라고 적힌 블록이 있었다 -- 진짜 2/11 을 덮으면 안 된다.
        if day is None or abs((day.year * 12 + day.month) - (tab_year * 12 + tab_month)) > 1:
            continue
        slots = [c for c in range(1, 40) if _cell(grid, header, c) == HEADER_MARK]
        groups, seen = [], set()
        for slot in slots:
            keyword = _cell(grid, header + 1, slot)
            if not keyword or keyword.startswith("#"):
                continue
            stocks = []
            for i in range(1, MAX_STOCKS + 1):
                row = header + 1 + i
                name = clean_name(_cell(grid, row, slot))
                if not name or name == HEADER_MARK:
                    break
                rate = parse_rate(_cell(grid, row, slot + 1))
                if rate is None:
                    continue
                stocks.append({"name": name, "rate": rate})
            if not stocks:
                continue
            names = frozenset(s["name"] for s in stocks)
            if names in seen:          # 같은 그룹이 여러 칸에 앉은 블록 (9/22)
                continue
            seen.add(names)
            stocks.sort(key=lambda s: -s["rate"])
            groups.append({"keyword": keyword, "stocks": stocks})
        if groups and len(groups) >= len(out.get(day, [])):
            out[day] = groups
    return out


def read_sheet(book) -> dict[date, list[dict]]:
    days: dict[date, list[dict]] = {}
    for title in book.titles():
        ym = tab_year_month(title)
        if ym is None:
            continue
        gid = book.gid_for_title(title)
        grid = book.read_formatted(gid, f"A1:AN{SEARCH_ROWS}")
        found = days_from_grid(grid, *ym)
        print(f"  탭 '{title}': {len(found)}일")
        for day, groups in found.items():
            if len(groups) >= len(days.get(day, [])):
                days[day] = groups
    return days


# ---------------------------------------------------------------- 이어짐

def norm_keyword(text: str) -> str:
    """괄호와 공백을 뺀 이름. '반도체 기판·PCB (FC-BGA)' == '반도체기판·PCB'."""
    return re.sub(r"\s+", "", re.sub(r"\(.*?\)", "", text or ""))


def head_word(text: str) -> str:
    """이름의 첫 마디. '반도체 기판·AI PCB' -> '반도체기판'."""
    return norm_keyword(re.split(r"[·/]", text or "")[0])


def same_theme(a: dict, b: dict) -> bool:
    """이름이 같거나, 종목이 OVERLAP 개 넘게 겹치거나, 첫 마디가 같고 한 종목이라도
    겹치면 같은 테마.

    셋째 규칙은 9/21 '반도체 기판·AI PCB' 와 9/22 '반도체 기판·PCB(FC-BGA/SOCAMM)'
    때문이다. 종목은 하나만 겹쳤지만 같은 테마다. 첫 마디만으로 묶으면 '반도체
    소재' 와 '반도체 기판' 이 갈리지 않으므로 겹치는 종목도 요구한다.
    """
    if norm_keyword(a["keyword"]) == norm_keyword(b["keyword"]):
        return True
    na = {s["name"] for s in a["stocks"]}
    nb = {s["name"] for s in b["stocks"]}
    shared = len(na & nb)
    if shared >= OVERLAP:
        return True
    return shared >= 1 and head_word(a["keyword"]) == head_word(b["keyword"])


def link_streaks(days: dict[date, list[dict]], trading: list[date]) -> list[dict]:
    """테마-일 목록. 각 항목에 streak(N일차)과 continued(다음 거래일에도 있나).

    trading 은 실제 장이 선 날(일봉에서 뽑은 것). 시트의 두 날 사이에 거래일이
    끼어 있으면 그 사이를 모르므로 이어짐을 끊는다. 마지막 날은 다음 날을 아직
    모르므로 continued 를 None 으로 둔다.
    """
    order = {d: i for i, d in enumerate(trading)}
    sheet_days = sorted(days)
    rows: list[dict] = []
    prev_day, prev_rows = None, []
    for day in sheet_days:
        adjacent = (prev_day is not None and day in order and prev_day in order
                    and order[day] - order[prev_day] == 1)
        today_rows = []
        for g in days[day]:
            streak, lineage = 1, None
            if adjacent:
                for p in prev_rows:
                    if same_theme(p["group"], g):
                        if p["streak"] + 1 > streak:
                            streak, lineage = p["streak"] + 1, p["lineage"]
            row = {"day": day, "group": g, "streak": streak,
                   "lineage": lineage or f"{day:%Y%m%d}:{g['keyword']}",
                   "continued": None}
            today_rows.append(row)
        # 사이에 모르는 거래일이 끼어 있으면 앞날의 이어짐은 모른다(None 그대로).
        if adjacent:
            for p in prev_rows:
                p["continued"] = any(same_theme(p["group"], g) for g in days[day])
        rows.extend(today_rows)
        prev_day, prev_rows = day, today_rows
    return rows


def only_trading(days: dict[date, list[dict]], trading: list[date]) -> dict[date, list[dict]]:
    """장이 안 선 날의 블록을 뺀다.

    휴장일 관문이 붙기 전에는 파이프라인이 추석(9/24·25)에도 돌아 전날 것과
    같은 블록을 적었다. 그대로 세면 같은 테마가 사흘 연속 나온 것이 된다.
    일봉이 하나도 없으면(네트워크 실패) 거를 근거가 없으므로 그대로 둔다.
    """
    if not trading:
        return days
    opened = set(trading)
    return {d: g for d, g in days.items() if d in opened}


# ---------------------------------------------------------------- 수익률

def fetch_bars(code: str, start: date, end: date) -> list[dict]:
    url = (f"{DAY_CHART_URL.format(code=code)}?startDateTime={start:%Y%m%d}0000"
           f"&endDateTime={end + timedelta(days=1):%Y%m%d}0000")
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                rows = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as exc:  # noqa: BLE001 - 한 종목 실패로 전체를 멈추지 않는다
            if attempt == 2:
                print(f"  [주의] {code} 일봉 실패: {exc}", file=sys.stderr)
                return []
            time.sleep(2 * (attempt + 1))
    return to_bars(rows if isinstance(rows, list) else [])


def to_bars(rows: list[dict]) -> list[dict]:
    bars = []
    for r in rows:
        try:
            d = str(r.get("localDate") or "")
            o, c = float(r.get("openPrice") or 0), float(r.get("closePrice") or 0)
        except (TypeError, ValueError):
            continue
        if len(d) != 8 or o <= 0 or c <= 0:
            continue
        bars.append({"date": date(int(d[:4]), int(d[4:6]), int(d[6:])), "open": o, "close": c})
    bars.sort(key=lambda b: b["date"])
    return bars


def outcome(bars: list[dict], day: date) -> dict | None:
    """기준일 day 의 종가에서 본 다음 날 시가 진입 수익률(%). 모자라면 그 칸만 None."""
    idx = next((i for i, b in enumerate(bars) if b["date"] == day), None)
    if idx is None or idx + 1 >= len(bars):
        return None
    base, entry = bars[idx]["close"], bars[idx + 1]["open"]
    out = {"gap": (entry / base - 1) * 100}
    for h in HORIZONS:
        j = idx + h
        out[f"r{h}"] = (bars[j]["close"] / entry - 1) * 100 if j < len(bars) else None
    return out


def attach_outcomes(rows: list[dict], bars_by_code: dict[str, list[dict]],
                    code_map: dict[str, str]) -> None:
    for row in rows:
        per = []
        for s in row["group"]["stocks"]:
            code = code_map.get(s["name"])
            res = outcome(bars_by_code.get(code, []), row["day"]) if code else None
            per.append(res)
        row["leader"] = per[0] if per else None
        keys = ["gap"] + [f"r{h}" for h in HORIZONS]
        avg = {}
        for k in keys:
            vals = [p[k] for p in per if p and p.get(k) is not None]
            avg[k] = statistics.fmean(vals) if vals else None
        row["group_avg"] = avg if any(v is not None for v in avg.values()) else None


# ---------------------------------------------------------------- 집계

def summarize(values: list[float]) -> dict | None:
    if not values:
        return None
    return {"n": len(values), "mean": round(statistics.fmean(values), 2),
            "median": round(statistics.median(values), 2),
            "win": round(sum(v > 0 for v in values) / len(values) * 100, 1)}


def bucket_stats(rows: list[dict], key) -> dict:
    """key(row) -> 버킷 이름. 버킷마다 대장주/그룹평균의 gap·r1·r3·r5 와 이어짐 확률."""
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        b = key(r)
        if b is not None:
            buckets.setdefault(b, []).append(r)
    out = {}
    for name, rs in sorted(buckets.items()):
        cont = [r["continued"] for r in rs if r["continued"] is not None]
        entry = {"n": len(rs),
                 "continue_pct": round(sum(cont) / len(cont) * 100, 1) if cont else None}
        for who in ("leader", "group_avg"):
            entry[who] = {}
            for k in ["gap"] + [f"r{h}" for h in HORIZONS]:
                vals = [r[who][k] for r in rs if r.get(who) and r[who].get(k) is not None]
                entry[who][k] = summarize(vals)
        out[name] = entry
    return out


def streak_bucket(r: dict) -> str:
    return f"{min(r['streak'], 3)}일차" + ("+" if r["streak"] >= 3 else "")


def leader_rate_bucket(r: dict) -> str:
    top = r["group"]["stocks"][0]["rate"]
    if top >= LIMIT_UP:
        return "a.상한가"
    if top >= 15:
        return "b.15~30%"
    return "c.15% 미만"


def gap_bucket(r: dict) -> str | None:
    """대장주가 다음 날 시가에서 얼마나 뜨고 시작했나. 갭이 클수록 시가 매수는 늦다."""
    g = (r.get("leader") or {}).get("gap")
    if g is None:
        return None
    if g < 0:
        return "a.갭하락"
    if g < 3:
        return "b.0~3%"
    if g < 8:
        return "c.3~8%"
    return "d.8%+"


def size_bucket(r: dict) -> str:
    n = len(r["group"]["stocks"])
    return "a.5종목+" if n >= 5 else ("b.3~4종목" if n >= 3 else "c.1~2종목")


def lineages(rows: list[dict], since: date, top: int = 12) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["lineage"], []).append(r)
    out = []
    for rs in by.values():
        rs.sort(key=lambda r: r["day"])
        if rs[-1]["day"] < since or len(rs) < 2:
            continue
        out.append({"days": len(rs), "from": rs[0]["day"].isoformat(),
                    "to": rs[-1]["day"].isoformat(),
                    "names": list(dict.fromkeys(r["group"]["keyword"] for r in rs))[-3:],
                    "leaders": list(dict.fromkeys(r["group"]["stocks"][0]["name"]
                                                  for r in rs))[:4]})
    out.sort(key=lambda x: (x["days"], x["to"]), reverse=True)
    return out[:top]


def current(rows: list[dict], by_streak: dict) -> list[dict]:
    """가장 최근 날의 테마들 + 같은 일차의 과거 성적. 봇이 읽는 자리."""
    if not rows:
        return []
    last = max(r["day"] for r in rows)
    out = []
    for r in rows:
        if r["day"] != last:
            continue
        b = streak_bucket(r)
        hist = by_streak.get(b, {})
        lead = hist.get("leader", {})
        out.append({
            "keyword": r["group"]["keyword"],
            "streak": r["streak"],
            "bucket": b,
            "leader": r["group"]["stocks"][0]["name"],
            "history": {
                "n": hist.get("n"),
                "continue_pct": hist.get("continue_pct"),
                "leader_r1": lead.get("r1"),
                "leader_r3": lead.get("r3"),
            },
        })
    return out


def build(days: dict[date, list[dict]], bars_by_code: dict[str, list[dict]],
          code_map: dict[str, str], generated_at: str) -> dict:
    trading = sorted({b["date"] for bars in bars_by_code.values() for b in bars})
    days = only_trading(days, trading)
    rows = link_streaks(days, trading)
    attach_outcomes(rows, bars_by_code, code_map)
    by_streak = bucket_stats(rows, streak_bucket)
    names = {s["name"] for gs in days.values() for g in gs for s in g["stocks"]}
    priced = {n for n in names if bars_by_code.get(code_map.get(n, ""))}
    last = max(days) if days else None
    return {
        "generated_at": generated_at,
        "period": {"from": min(days).isoformat() if days else None,
                   "to": last.isoformat() if last else None},
        "entry": "다음 거래일 시가",
        "sample": {"days": len(days), "theme_days": len(rows),
                   "stocks": len(names), "priced_stocks": len(priced)},
        "by_streak": by_streak,
        "by_leader_rate": bucket_stats(rows, leader_rate_bucket),
        "by_size": bucket_stats(rows, size_bucket),
        "by_gap": bucket_stats(rows, gap_bucket),
        "lineages": lineages(rows, (last or date.today()) - timedelta(days=60)),
        "current": current(rows, by_streak),
    }


# ---------------------------------------------------------------- 보고서

def _fmt(s: dict | None, key="mean") -> str:
    if not s:
        return "-"
    return f"{s[key]:+.2f}%"


def report_md(stats: dict) -> str:
    p, smp = stats["period"], stats["sample"]
    lines = [
        f"# 테마 지속성 ({p['from']} ~ {p['to']})",
        "",
        f"테마-일 {smp['theme_days']}건 / {smp['days']}거래일, 종목 {smp['stocks']}개 중 "
        f"일봉 확보 {smp['priced_stocks']}개. 진입은 **{stats['entry']}**.",
        "",
    ]

    def table(title: str, data: dict):
        lines.extend([f"## {title}", "",
                      "| 구분 | 표본 | 다음날 이어짐 | 대장 갭 | 대장 D+1 | 대장 D+1 승률 | 대장 D+3 | 대장 D+5 | 그룹 D+1 | 그룹 D+3 |",
                      "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
        for name, e in data.items():
            ld, ga = e["leader"], e["group_avg"]
            cont = f"{e['continue_pct']:.0f}%" if e["continue_pct"] is not None else "-"
            win = f"{ld['r1']['win']:.0f}%" if ld.get("r1") else "-"
            lines.append(f"| {name.split('.', 1)[-1]} | {e['n']} | {cont} | {_fmt(ld.get('gap'))} | "
                         f"{_fmt(ld.get('r1'))} | {win} | {_fmt(ld.get('r3'))} | {_fmt(ld.get('r5'))} | "
                         f"{_fmt(ga.get('r1'))} | {_fmt(ga.get('r3'))} |")
        lines.append("")

    table("며칠째 이어진 테마인가", stats["by_streak"])
    table("대장주 상승률", stats["by_leader_rate"])
    table("그룹 크기", stats["by_size"])
    table("다음 날 시가 갭 (대장주)", stats["by_gap"])

    if stats["lineages"]:
        lines.extend(["## 최근 60일 오래 간 테마", "",
                      "| 일수 | 기간 | 이름 | 대장주 |", "|---:|---|---|---|"])
        for ln in stats["lineages"]:
            lines.append(f"| {ln['days']} | {ln['from'][5:]}~{ln['to'][5:]} | "
                         f"{' → '.join(ln['names'])} | {', '.join(ln['leaders'])} |")
        lines.append("")

    if stats["current"]:
        lines.extend(["## 오늘 테마", "", "| 테마 | 일차 | 대장주 | 같은 일차 이어짐 | 같은 일차 대장 D+1 |",
                      "|---|---:|---|---:|---:|"])
        for c in stats["current"]:
            h = c["history"]
            cont = f"{h['continue_pct']:.0f}%" if h.get("continue_pct") is not None else "-"
            lines.append(f"| {c['keyword']} | {c['streak']} | {c['leader']} | {cont} | "
                         f"{_fmt(h.get('leader_r1'))} |")
        lines.append("")
    lines.append("평균은 단순 평균이다. 표본이 30건 아래인 칸은 우연일 수 있다.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 실행

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="latest.json / report.md 를 쓸 곳")
    ap.add_argument("--days", type=int, default=0, help="최근 N일만 (0 = 전부)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--affinity", help="종목별 과거 키워드 기록을 여기에 쓴다 (theme_fit)")
    args = ap.parse_args()

    from feed_export import load_code_map
    from sheets import Spreadsheet

    print("시트 읽는 중")
    days = read_sheet(Spreadsheet(DAILY_ID))
    if args.affinity:
        # 오배정 판정(theme_fit)이 읽는 기록. --days 로 자르기 전 전체에서 만들고,
        # 휴장일에 잘못 적힌 블록은 뺀다(같은 날이 두 번 세어진다).
        import krx_holidays
        from theme_fit import build_affinity
        opened = {d: g for d, g in days.items() if not krx_holidays.holiday_name(d)}
        Path(args.affinity).write_text(
            json.dumps(build_affinity(opened), ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8")
        print(f"  오배정 기록 {args.affinity} 갱신 ({len(opened)}일)")
    if args.days:
        cut = date.today() - timedelta(days=args.days)
        days = {d: g for d, g in days.items() if d >= cut}
    if not days:
        print("[중단] 시트에서 읽은 날이 없습니다.", file=sys.stderr)
        return 1
    print(f"  {min(days)} ~ {max(days)}, {len(days)}일")

    code_map = load_code_map()
    names = {s["name"] for gs in days.values() for g in gs for s in g["stocks"]}
    codes = sorted({code_map[n] for n in names if n in code_map})
    print(f"종목 {len(names)}개 중 코드 {len(codes)}개 — 일봉 받는 중")
    start, end = min(days) - timedelta(days=7), datetime.now(KST).date()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        bars = dict(zip(codes, pool.map(lambda c: fetch_bars(c, start, end), codes)))

    stats = build(days, bars, code_map, datetime.now(KST).isoformat(timespec="seconds"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    report = report_md(stats)
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
