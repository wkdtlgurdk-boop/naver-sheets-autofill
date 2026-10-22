#!/usr/bin/env python3
"""테마에 실린 종목의 5·10거래일 뒤 성적. Daily 시트 전체(전종목)를 되짚는다.

    python analysis/theme_horizon.py            # analysis/sheet_days.json + 네이버 일봉
    python analysis/theme_horizon.py --max-rate 30   # 상한가만 빼고 25~30% 는 넣기

sheet_days.json 은 theme-stats.yml 을 dump_only 로 돌려 만든다(시트 인증은
Actions 에만 있다).

## 무엇을 세는가

시트 블록(하루)의 그룹마다 적힌 종목 하나하나가 '편입' 한 건이다. 같은 날 두
그룹에 앉은 종목은 한 번만 센다. 그날 상승률이 --max-rate(기본 25%) 이상인
종목 -- 상한가와 25~30% 급등 -- 은 뺀다.

    기준 D      시트에 실린 날
    종가 기준   종가(D+N) / 종가(D) - 1          실린 날 종가에 샀다면
    시가 기준   종가(D+N) / 시가(D+1) - 1        다음 날 아침 시가에 샀다면
    최고/최저   D+1~D+N 고가 최대·저가 최소 / 종가(D) - 1
    초과        종가 기준 - 같은 시장 지수의 같은 기간 수익률

N 거래일은 지수 일봉의 날짜로 센다. 그 날에 종목 봉이 없으면(거래정지) 그
칸은 비운다. 하루 등락이 ±31% 를 넘는 봉이 창 안에 있으면(액면분할·병합으로
가격이 이어지지 않은 것) 그 건을 뺀다.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from feed_export import load_code_map  # noqa: E402
from theme_stats import HEADERS, head_word, link_streaks  # noqa: E402

HORIZONS = (5, 10)
CHART = "https://api.stock.naver.com/chart/domestic/{kind}/{code}/day"
BASIC = "https://m.stock.naver.com/api/stock/{code}/basic"


def get_json(url: str):
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                print(f"  [주의] {url}: {exc}", file=sys.stderr)
                return None
            time.sleep(2 * (attempt + 1))


def fetch_bars(kind: str, code: str, start: date, end: date) -> list[dict]:
    url = (f"{CHART.format(kind=kind, code=code)}?startDateTime={start:%Y%m%d}0000"
           f"&endDateTime={end + timedelta(days=1):%Y%m%d}0000")
    rows = get_json(url) or []
    bars = []
    for r in rows if isinstance(rows, list) else []:
        d = str(r.get("localDate") or "")
        try:
            o, h, l, c = (float(r.get(k) or 0) for k in
                          ("openPrice", "highPrice", "lowPrice", "closePrice"))
        except (TypeError, ValueError):
            continue
        if len(d) != 8 or min(o, h, l, c) <= 0:
            continue
        bars.append({"d": f"{d[:4]}-{d[4:6]}-{d[6:]}", "o": o, "h": h, "l": l, "c": c})
    bars.sort(key=lambda b: b["d"])
    return bars


def fetch_market(code: str) -> str:
    j = get_json(BASIC.format(code=code)) or {}
    t = (j.get("stockExchangeType") or {}).get("code") or j.get("stockExchangeName") or ""
    return "KOSDAQ" if "KQ" in t or "KOSDAQ" in t.upper() else "KOSPI"


def load_or_fetch(cache: Path, codes: list[str], start: date, end: date, workers: int):
    data = json.loads(cache.read_text()) if cache.exists() else {"bars": {}, "market": {}}
    need = [c for c in codes if c not in data["bars"]]
    print(f"일봉 {len(codes)}종목 중 새로 받을 것 {len(need)}")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for c, b in zip(need, pool.map(lambda c: fetch_bars("item", c, start, end), need)):
            data["bars"][c] = b
        needm = [c for c in codes if c not in data["market"]]
        for c, m in zip(needm, pool.map(fetch_market, needm)):
            data["market"][c] = m
    for idx in ("KOSPI", "KOSDAQ"):
        if idx not in data.setdefault("index", {}):
            data["index"][idx] = fetch_bars("index", idx, start, end)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data))
    return data


# ---------------------------------------------------------------- 계산

def window(bars_by_day: dict, cal: list[str], i0: int, n: int):
    """기준일 cal[i0] 에서 n 거래일. 모자라거나 정지·분할이면 None."""
    if i0 + n >= len(cal):
        return None
    days = cal[i0:i0 + n + 1]
    seq = [bars_by_day.get(d) for d in days]
    if seq[0] is None or seq[1] is None or seq[-1] is None:
        return None
    prev = seq[0]["c"]
    for b in seq[1:]:
        if b is None:
            continue
        if abs(b["c"] / prev - 1) > 0.31:
            return "split"
        prev = b["c"]
    live = [b for b in seq[1:] if b]
    base = seq[0]["c"]
    return {
        "c2c": (seq[-1]["c"] / base - 1) * 100,
        "o2c": (seq[-1]["c"] / seq[1]["o"] - 1) * 100,
        "max_up": (max(b["h"] for b in live) / base - 1) * 100,
        "max_dn": (min(b["l"] for b in live) / base - 1) * 100,
    }


def index_ret(idx: dict, cal: list[str], i0: int, n: int):
    a, b = idx.get(cal[i0]), idx.get(cal[i0 + n]) if i0 + n < len(cal) else None
    return (b["c"] / a["c"] - 1) * 100 if a and b else None


def build_events(days: dict, code_map: dict, data: dict, max_rate: float):
    cal = [b["d"] for b in data["index"]["KOSPI"]]
    pos = {d: i for i, d in enumerate(cal)}
    idx = {k: {b["d"]: b for b in v} for k, v in data["index"].items()}
    by_code = {c: {b["d"]: b for b in bs} for c, bs in data["bars"].items()}

    # 테마 일차(theme_stats 와 같은 규칙)
    dd = {date.fromisoformat(k): v for k, v in days.items() if k in pos}
    rows = link_streaks(dd, [date.fromisoformat(d) for d in cal])
    streak = {(r["day"].isoformat(), r["group"]["keyword"]): r["streak"] for r in rows}

    last_seen: dict[str, int] = {}
    events, skipped = [], {"no_code": 0, "excluded_rate": 0, "split": 0, "dup": 0}
    for day in sorted(k for k in days if k in pos):
        i0 = pos[day]
        seen_today = set()
        for g in days[day]:
            stocks = sorted(g["stocks"], key=lambda s: -s["rate"])
            for rank, s in enumerate(stocks, 1):
                code = code_map.get(s["name"])
                if not code:
                    skipped["no_code"] += 1
                    continue
                if code in seen_today:
                    skipped["dup"] += 1
                    continue
                seen_today.add(code)
                fresh = code not in last_seen or i0 - last_seen[code] > 20
                last_seen[code] = i0
                if s["rate"] >= max_rate:
                    skipped["excluded_rate"] += 1
                    continue
                mk = data["market"].get(code, "KOSPI")
                ev = {"day": day, "keyword": g["keyword"], "head": head_word(g["keyword"]),
                      "name": s["name"], "code": code, "market": mk, "rate": s["rate"],
                      "rank": rank, "size": len(stocks),
                      "streak": streak.get((day, g["keyword"]), 1), "fresh": fresh}
                bad = False
                for n in HORIZONS:
                    w = window(by_code.get(code, {}), cal, i0, n)
                    if w == "split":
                        bad = True
                        break
                    ir = index_ret(idx[mk], cal, i0, n)
                    for k in ("c2c", "o2c", "max_up", "max_dn"):
                        ev[f"{k}{n}"] = w[k] if w else None
                    ev[f"ex{n}"] = (w["c2c"] - ir) if w and ir is not None else None
                if bad:
                    skipped["split"] += 1
                    continue
                events.append(ev)
    return events, skipped, cal


# ---------------------------------------------------------------- 집계

def summ(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    q = statistics.quantiles(vals, n=4) if len(vals) > 3 else [vals[0]] * 3
    return {"n": len(vals), "mean": statistics.fmean(vals), "med": statistics.median(vals),
            "win": sum(v > 0 for v in vals) / len(vals) * 100,
            "big": sum(v >= 10 for v in vals) / len(vals) * 100,
            "bad": sum(v <= -10 for v in vals) / len(vals) * 100,
            "q1": q[0], "q3": q[2]}


def pct(x, sign=True):
    return "-" if x is None else (f"{x:+.2f}%" if sign else f"{x:.0f}%")


def table(title, groups, note=""):
    lines = [f"## {title}", ""]
    if note:
        lines += [note, ""]
    lines += ["| 구분 | 표본 | D+5 평균 | D+5 중앙 | D+5 승률 | D+5 초과 | D+10 평균 | D+10 중앙 | D+10 승률 | D+10 초과 | +10%↑ | -10%↓ |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, evs in groups:
        a5, a10 = summ([e["c2c5"] for e in evs]), summ([e["c2c10"] for e in evs])
        x5, x10 = summ([e["ex5"] for e in evs]), summ([e["ex10"] for e in evs])
        if not a5:
            continue
        lines.append(
            f"| {name} | {a5['n']} | {pct(a5['mean'])} | {pct(a5['med'])} | {pct(a5['win'], 0)} | "
            f"{pct(x5 and x5['mean'])} | {pct(a10 and a10['mean'])} | {pct(a10 and a10['med'])} | "
            f"{pct(a10 and a10['win'], 0)} | {pct(x10 and x10['mean'])} | "
            f"{pct(a10 and a10['big'], 0)} | {pct(a10 and a10['bad'], 0)} |")
    return lines + [""]


def bucket(events, key, order=None):
    out: dict[str, list] = {}
    for e in events:
        k = key(e)
        if k is not None:
            out.setdefault(k, []).append(e)
    keys = order or sorted(out)
    return [(k, out[k]) for k in keys if k in out]


def rate_b(e):
    r = e["rate"]
    for lo, hi in ((20, 25), (15, 20), (10, 15), (5, 10), (0, 5)):
        if r >= lo:
            return f"{lo}~{hi}%"
    return "하락(0% 미만)"


def report(events, skipped, period, max_rate) -> str:
    L = [f"# 테마 편입 후 5·10거래일 성적 ({period[0]} ~ {period[1]})", "",
         f"Daily 시트에 실린 **전종목** {len(events)}건. 그날 상승률 {max_rate:g}% 이상"
         f"(상한가·급등) {skipped['excluded_rate']}건 제외, 같은 날 중복 {skipped['dup']}건, "
         f"코드 없음 {skipped['no_code']}건, 분할·병합 의심 {skipped['split']}건 제외.", "",
         "기본 수익률은 **실린 날 종가 → N거래일 뒤 종가**. 초과 = 같은 시장 지수 대비.", ""]

    L += ["## 전체", "",
          "| 기준 | 기간 | 표본 | 평균 | 중앙값 | 승률 | 1사분위 | 3사분위 | +10%↑ | -10%↓ |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for label, k in (("실린 날 종가", "c2c"), ("다음 날 시가", "o2c"), ("지수 대비 초과", "ex"),
                     ("기간 중 최고(고가)", "max_up"), ("기간 중 최저(저가)", "max_dn")):
        for n in HORIZONS:
            s = summ([e[f"{k}{n}"] for e in events])
            if s:
                L.append(f"| {label} | D+{n} | {s['n']} | {pct(s['mean'])} | {pct(s['med'])} | "
                         f"{pct(s['win'], 0)} | {pct(s['q1'])} | {pct(s['q3'])} | "
                         f"{pct(s['big'], 0)} | {pct(s['bad'], 0)} |")
    L.append("")

    L += table("그날 상승률", bucket(events, rate_b,
               ["20~25%", "15~20%", "10~15%", "5~10%", "0~5%", "하락(0% 미만)"]))
    L += table("그룹 안 순위", bucket(events, lambda e: "1위(대장)" if e["rank"] == 1 else
               ("2~3위" if e["rank"] <= 3 else "4위 이하"), ["1위(대장)", "2~3위", "4위 이하"]),
               "순위는 상한가·급등주를 빼기 전 그룹 안 상승률 순위다.")
    L += table("테마 일차", bucket(events, lambda e: f"{min(e['streak'], 3)}일차" +
               ("+" if e["streak"] >= 3 else "")))
    L += table("처음 실렸나", bucket(events, lambda e: "첫 편입(20거래일 내 처음)" if e["fresh"]
               else "재편입", ["첫 편입(20거래일 내 처음)", "재편입"]))
    L += table("그룹 크기", bucket(events, lambda e: "5종목+" if e["size"] >= 5 else
               ("3~4종목" if e["size"] >= 3 else "1~2종목"), ["5종목+", "3~4종목", "1~2종목"]))
    L += table("시장", bucket(events, lambda e: e["market"]))
    L += table("분기", bucket(events, lambda e: f"{e['day'][:4]} Q{(int(e['day'][5:7]) - 1) // 3 + 1}"))

    # 테마(키워드 첫 마디)별, 표본 40건 이상
    heads = bucket(events, lambda e: e["head"])
    heads = [(k, v) for k, v in heads if len(v) >= 40]
    heads.sort(key=lambda kv: -(summ([e["c2c10"] for e in kv[1]]) or {"mean": -99})["mean"])
    L += table("테마별 (키워드 첫 마디, 40건 이상, D+10 평균 순)", heads)

    L.append("평균은 단순 평균. 승률은 0% 초과 비율. 표본 30건 미만 칸은 우연일 수 있다.")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default=str(HERE / "sheet_days.json"))
    ap.add_argument("--cache", default=str(HERE / "cache" / "bars.json"))
    ap.add_argument("--out", default=str(HERE / "theme_horizon"))
    ap.add_argument("--max-rate", type=float, default=25.0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    days = json.loads(Path(args.sheet).read_text(encoding="utf-8"))
    code_map = load_code_map()
    names = {s["name"] for gs in days.values() for g in gs for s in g["stocks"]}
    codes = sorted({code_map[n] for n in names if n in code_map})
    first = date.fromisoformat(min(days)) - timedelta(days=10)
    data = load_or_fetch(Path(args.cache), codes, first, date.today(), args.workers)

    events, skipped, cal = build_events(days, code_map, data, args.max_rate)
    period = (min(e["day"] for e in events), max(e["day"] for e in events))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    md = report(events, skipped, period, args.max_rate)
    (out / "report.md").write_text(md, encoding="utf-8")
    with open(out / "events.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(events[0]))
        w.writeheader()
        for e in events:
            w.writerow({k: (round(v, 2) if isinstance(v, float) else v) for k, v in e.items()})
    # 종목별 요약: 편입 횟수와 평균 성적
    by: dict[str, list] = {}
    for e in events:
        by.setdefault(e["code"], []).append(e)
    with open(out / "stocks.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "market", "편입수", "D+5평균", "D+5승률", "D+10평균",
                    "D+10승률", "D+10초과평균", "주요테마"])
        for code, evs in sorted(by.items(), key=lambda kv: -len(kv[1])):
            a5, a10 = summ([e["c2c5"] for e in evs]), summ([e["c2c10"] for e in evs])
            x10 = summ([e["ex10"] for e in evs])
            heads = statistics.multimode([e["head"] for e in evs])[:2]
            r = lambda s, k: round(s[k], 2) if s else ""  # noqa: E731
            w.writerow([code, evs[-1]["name"], evs[-1]["market"], len(evs), r(a5, "mean"),
                        r(a5, "win"), r(a10, "mean"), r(a10, "win"), r(x10, "mean"),
                        "/".join(heads)])
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
