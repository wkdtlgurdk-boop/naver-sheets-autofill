#!/usr/bin/env python3
"""테마 편입 후 +20% 간 종목의 공통점. theme_horizon.py 결과(events.csv)를 읽는다.

    python analysis/theme_horizon.py      # 먼저
    python analysis/winner_traits.py

'20% 종목' = 실린 날 종가 대비 D+10 종가가 +20% 이상(기본). D+5 기준도 같이 본다.
구간마다 '20% 종목이 나온 비율'을 전체 평균(기준율)과 견준다.

    시총       그날 종가 x 상장주식수. 주식수는 지금 값(네이버 시총 / 현재가)이라
               그사이 증자·감자가 있던 종목은 조금 어긋난다.
    표출 빈도  그날 전 20·60거래일 동안 Daily 시트에 실린 날 수(그날은 뺀다)
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from theme_horizon import get_json  # noqa: E402

INTEGRATION = "https://m.stock.naver.com/api/stock/{code}/integration"
WIN = 20.0


def to_num(text: str) -> float | None:
    """'3,668억' / '1조 2,345억' -> 억 단위 숫자. '8,370' -> 8370."""
    t = str(text or "").replace(",", "").strip()
    jo = re.search(r"([\d.]+)조", t)
    eok = re.search(r"([\d.]+)억", t)
    if jo or eok:
        return (float(jo.group(1)) * 10000 if jo else 0) + (float(eok.group(1)) if eok else 0)
    m = re.match(r"[\d.]+", t)
    return float(m.group()) if m else None


def fetch_shares(code: str) -> float | None:
    j = get_json(INTEGRATION.format(code=code)) or {}
    info = {x.get("code"): x.get("value") for x in j.get("totalInfos") or []}
    mcap = to_num(info.get("marketValue"))
    basic = get_json(f"https://m.stock.naver.com/api/stock/{code}/basic") or {}
    price = to_num(basic.get("closePrice"))
    if not mcap or not price:
        return None
    return mcap * 1e8 / price


def load_shares(cache: Path, codes: list[str]) -> dict[str, float | None]:
    data = json.loads(cache.read_text()) if cache.exists() else {}
    need = [c for c in codes if c not in data]
    print(f"상장주식수 {len(codes)}종목 중 새로 받을 것 {len(need)}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        for c, s in zip(need, pool.map(fetch_shares, need)):
            data[c] = s
    cache.write_text(json.dumps(data))
    return data


def f(x):
    return float(x) if x not in ("", None) else None


def main() -> int:
    events = list(csv.DictReader(open(HERE / "theme_horizon" / "events.csv", encoding="utf-8-sig")))
    days = json.loads((HERE / "sheet_days.json").read_text(encoding="utf-8"))
    bars = json.loads((HERE / "cache" / "bars.json").read_text())
    cal = [b["d"] for b in bars["index"]["KOSPI"]]
    pos = {d: i for i, d in enumerate(cal)}
    close = {c: {b["d"]: b["c"] for b in bs} for c, bs in bars["bars"].items()}
    shares = load_shares(HERE / "cache" / "shares.json", sorted({e["code"] for e in events}))

    # 이름 -> 시트에 실린 거래일 위치들 (코드 대신 이름: 시트에는 이름만 있다)
    from feed_export import load_code_map
    code_map = load_code_map()
    seen: dict[str, set[int]] = {}
    for d, gs in days.items():
        if d not in pos:
            continue
        for g in gs:
            for s in g["stocks"]:
                c = code_map.get(s["name"])
                if c:
                    seen.setdefault(c, set()).add(pos[d])

    rows = []
    for e in events:
        i0 = pos[e["day"]]
        hits = seen.get(e["code"], set())
        sh = shares.get(e["code"])
        px = close.get(e["code"], {}).get(e["day"])
        rows.append({
            **e,
            "rate": float(e["rate"]), "rank": int(e["rank"]), "size": int(e["size"]),
            "streak": int(e["streak"]),
            "c2c5": f(e["c2c5"]), "c2c10": f(e["c2c10"]), "max_up10": f(e["max_up10"]),
            "prev20": sum(1 for i in hits if i0 - 20 <= i < i0),
            "prev60": sum(1 for i in hits if i0 - 60 <= i < i0),
            "total": len(hits),
            "after10": sum(1 for i in hits if i0 < i <= i0 + 10),
            "mcap": sh * px / 1e8 if sh and px else None,   # 억
        })
    rows10 = [r for r in rows if r["c2c10"] is not None]
    base10 = sum(r["c2c10"] >= WIN for r in rows10) / len(rows10) * 100
    rows5 = [r for r in rows if r["c2c5"] is not None]
    base5 = sum(r["c2c5"] >= WIN for r in rows5) / len(rows5) * 100

    L = [f"# 테마 편입 후 +{WIN:g}% 간 종목의 공통점", "",
         f"대상: 테마에 실린 종목 {len(rows10)}건 (그날 25% 이상 급등·상한가 제외). "
         f"D+10 종가 +{WIN:g}% 이상 **{sum(r['c2c10'] >= WIN for r in rows10)}건 "
         f"(기준율 {base10:.1f}%)**, D+5 기준 {sum(r['c2c5'] >= WIN for r in rows5)}건 "
         f"({base5:.1f}%).", "",
         "표의 '20%↑ 비율'이 기준율보다 높으면 그 구간에서 20% 종목이 더 자주 나왔다는 뜻이다.", ""]

    def table(title, key, order, note=""):
        L.extend([f"## {title}", ""] + ([note, ""] if note else []) + [
            "| 구분 | 표본 | D+10 20%↑ 비율 | 배수 | D+5 20%↑ 비율 | D+10 평균 | D+10 -10%↓ |",
            "|---|---:|---:|---:|---:|---:|---:|"])
        groups: dict[str, list] = {}
        for r in rows:
            k = key(r)
            if k is not None:
                groups.setdefault(k, []).append(r)
        for k in order:
            g = groups.get(k)
            if not g:
                continue
            g10 = [r for r in g if r["c2c10"] is not None]
            g5 = [r for r in g if r["c2c5"] is not None]
            if not g10:
                continue
            p10 = sum(r["c2c10"] >= WIN for r in g10) / len(g10) * 100
            p5 = sum(r["c2c5"] >= WIN for r in g5) / len(g5) * 100 if g5 else 0
            avg = sum(r["c2c10"] for r in g10) / len(g10)
            bad = sum(r["c2c10"] <= -10 for r in g10) / len(g10) * 100
            L.append(f"| {k} | {len(g10)} | {p10:.1f}% | {p10 / base10:.2f}x | {p5:.1f}% | "
                     f"{avg:+.2f}% | {bad:.0f}% |")
        L.append("")

    def cap_b(r):
        m = r["mcap"]
        if m is None:
            return None
        for lo, name in ((50000, "5조+"), (10000, "1조~5조"), (5000, "5천억~1조"),
                         (3000, "3천억~5천억"), (1500, "1500억~3천억"), (0, "1500억 미만")):
            if m >= lo:
                return name
    table("시가총액 (실린 날 기준)", cap_b,
          ["1500억 미만", "1500억~3천억", "3천억~5천억", "5천억~1조", "1조~5조", "5조+"])

    def cnt_b(n, cuts):
        for lo, name in cuts:
            if n >= lo:
                return name
    c20 = [(6, "6회+"), (3, "3~5회"), (1, "1~2회"), (0, "0회(처음)")]
    table("직전 20거래일 동안 테마에 실린 횟수", lambda r: cnt_b(r["prev20"], c20),
          ["0회(처음)", "1~2회", "3~5회", "6회+"])
    c60 = [(10, "10회+"), (5, "5~9회"), (2, "2~4회"), (1, "1회"), (0, "0회(처음)")]
    table("직전 60거래일 동안 테마에 실린 횟수", lambda r: cnt_b(r["prev60"], c60),
          ["0회(처음)", "1회", "2~4회", "5~9회", "10회+"])
    ct = [(20, "20회+"), (10, "10~19회"), (5, "5~9회"), (2, "2~4회"), (1, "1회")]
    table("전체 기간 동안 실린 횟수 (종목 성격)", lambda r: cnt_b(r["total"], ct),
          ["1회", "2~4회", "5~9회", "10~19회", "20회+"],
          "1년반 동안 총 몇 번 실린 종목인가. 그 뒤에 실린 것까지 세므로 '오른 뒤 또 실린' 효과가 섞인다.")

    # 시총 x 직전 20일 표출 교차
    L.extend(["## 시총 × 직전 20거래일 표출 (D+10 20%↑ 비율, 괄호는 표본)", "",
              "| 시총 \\ 표출 | 0회(처음) | 1~2회 | 3회+ |", "|---|---:|---:|---:|"])
    for cap in ["1500억 미만", "1500억~3천억", "3천억~5천억", "5천억~1조", "1조~5조", "5조+"]:
        cells = []
        for lo, hi in ((0, 0), (1, 2), (3, 999)):
            g = [r for r in rows10 if cap_b(r) == cap and lo <= r["prev20"] <= hi]
            cells.append(f"{sum(r['c2c10'] >= WIN for r in g) / len(g) * 100:.1f}% ({len(g)})"
                         if len(g) >= 15 else f"- ({len(g)})")
        L.append(f"| {cap} | " + " | ".join(cells) + " |")
    L.append("")

    def rate_b(r):
        for lo in (20, 15, 10, 5, 0):
            if r["rate"] >= lo:
                return f"{lo}~{lo + 5}%"
    table("그날 상승률", rate_b, ["0~5%", "5~10%", "10~15%", "15~20%", "20~25%"])
    table("그룹 크기", lambda r: "5종목+" if r["size"] >= 5 else ("3~4종목" if r["size"] >= 3 else "1~2종목"),
          ["1~2종목", "3~4종목", "5종목+"])
    table("시장", lambda r: r["market"], ["KOSDAQ", "KOSPI"])

    # 20% 종목이 많이 나온 테마 / 종목
    win = [r for r in rows10 if r["c2c10"] >= WIN]
    heads = Counter(r["head"] for r in rows10)
    wheads = Counter(r["head"] for r in win)
    L.extend(["## 20% 종목이 많이 나온 테마 (키워드 첫 마디, 20건 이상)", "",
              "| 테마 | 표본 | 20%↑ 건수 | 비율 | 배수 |", "|---|---:|---:|---:|---:|"])
    for h, n in sorted(heads.items(), key=lambda kv: -(wheads[kv[0]] / kv[1])):
        if n >= 20 and wheads[h] / n * 100 >= base10 * 1.3:
            L.append(f"| {h} | {n} | {wheads[h]} | {wheads[h] / n * 100:.1f}% | "
                     f"{wheads[h] / n * 100 / base10:.2f}x |")
    L.append("")

    by_stock = Counter(r["name"] for r in win)
    L.extend(["## 20% 를 여러 번 낸 종목", "", "| 종목 | 횟수 | 시장 | 주요 테마 |", "|---|---:|---|---|"])
    for name, n in by_stock.most_common(20):
        rs = [r for r in win if r["name"] == name]
        L.append(f"| {name} | {n} | {rs[0]['market']} | "
                 f"{'/'.join(h for h, _ in Counter(r['head'] for r in rs).most_common(2))} |")
    L.append("")

    # 20% 종목 vs 나머지 중앙값 비교
    import statistics as st
    rest = [r for r in rows10 if r["c2c10"] < WIN]
    L.extend(["## 20% 종목 vs 나머지 (중앙값)", "", "| 항목 | 20% 종목 | 나머지 |", "|---|---:|---:|"])
    for label, k in (("시총(억)", "mcap"), ("직전 20일 표출", "prev20"), ("직전 60일 표출", "prev60"),
                     ("그날 상승률(%)", "rate"), ("그룹 크기", "size"),
                     ("이후 10일 안에 다시 실린 횟수", "after10")):
        a = [r[k] for r in win if r[k] is not None]
        b = [r[k] for r in rest if r[k] is not None]
        L.append(f"| {label} | {st.median(a):,.1f} | {st.median(b):,.1f} |")
    L.append("")
    L.append("표본 30건 미만 칸은 우연일 수 있다.")

    md = "\n".join(L) + "\n"
    (HERE / "theme_horizon" / "winners.md").write_text(md, encoding="utf-8")
    with open(HERE / "theme_horizon" / "winners.csv", "w", newline="", encoding="utf-8-sig") as fh:
        keys = ["day", "name", "code", "market", "keyword", "rate", "mcap", "prev20", "prev60",
                "total", "c2c5", "c2c10", "max_up10"]
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in sorted(win, key=lambda r: -r["c2c10"]):
            w.writerow({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
