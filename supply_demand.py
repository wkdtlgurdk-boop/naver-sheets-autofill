#!/usr/bin/env python3
"""수급동향 시트: 외국인/기관 x 코스피/코스닥 순매수 상위 20종목, 최근 5거래일.

시트는 요일(월~금)이 아니라 **날짜** 5칸을 들고 있고, 하루가 지나면 제일 왼쪽
날짜를 버리고 오른쪽에 새 날짜를 붙이는 미끄러지는 창이다. 예전의 금주/전주
2블록 구조를 사용자가 걷어내면서 이렇게 바뀌었다.

요일 칸이 아니라 날짜 칸인 것이 중요하다. 요일 방식은 실행이 하루 빠지면 그
칸이 지난주 값을 단 채로 남아 한 주 밀린 줄 모르게 되는데, 날짜가 적혀 있으면
빠진 날이 그대로 눈에 보인다.

검증에 하나라도 실패하면 아무것도 쓰지 않는다.

    python supply_demand.py --dry-run    # 수집·검증·계획만
    python supply_demand.py              # 실제 기록
    python supply_demand.py --export supply_today.json   # 기록 + 오늘치 내보내기

--export 는 방금 시트에 쓴 것을 종목코드까지 붙여 한 파일로 남긴다.
feed_export.py 가 그것을 공개 저장소의 5일 창에 얹고, 아침 매수 후보
봇(volume-surge-telegram-bot/tradesignal)이 거기서 수급 신호를 센다. 시트에는
종목코드가 없어서, 코드를 아는 유일한 자리가 방금 받은 이 응답이다.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from sheets import Spreadsheet

KST = timezone(timedelta(hours=9))
SPREADSHEET_ID = "1F3SOlj6ER_JUOjrPdKxQeLWbAuHHf49eiB5xyl0rM7U"

BASE = "https://stock.naver.com/api/domestic/market/trend/trendForeignOrg"
HEADERS = {
    "Referer": "https://stock.naver.com/",
    "User-Agent": "Mozilla/5.0 supply-demand-autofill/4.0",
}

# 2026-09-07 실측. 전주 블록을 걷어낸 뒤로 두 탭의 행 배치가 완전히 같아졌다
# (예전에는 외인 30/76, 기관 28/73 으로 어긋나 있었다).
TABS = {"외인": {"gid": 0, "investor": "FOREIGNER"},
        "기관": {"gid": 1644270427, "investor": "ORGANIZATION"}}
MARKETS = {"KOSPI": {"date_row": 4, "first_row": 6},
           "KOSDAQ": {"date_row": 27, "first_row": 29}}

SLOTS = ["B", "D", "F", "H", "J"]   # 왼쪽이 가장 오래된 날, J 가 최신
ROWS = 20                           # 한 날짜의 종목 수
WIDTH = len(SLOTS) * 2              # B..K


def fetch(investor_type: str, market_type: str) -> list[dict]:
    qs = urllib.parse.urlencode({
        "investorType": investor_type, "tradeType": "KRX",
        "marketType": market_type, "startIdx": 0,
        "pageSize": ROWS, "periodType": "DAY",
    })
    req = urllib.request.Request(f"{BASE}?{qs}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["sections"]["buyRankList"]


class NotSettled(Exception):
    """오늘 확정치가 아직 아니다. 오류가 아니라 '아직'이다."""


def validate(rows: list[dict], today: str, label: str) -> None:
    """확정치가 아니면 NotSettled. 장중 실행·휴장일·데이터 지연을 여기서 막는다.

    장중에는 FOREIGNER 가 estimated=true 에 금액 0 인 오늘치를 주고,
    ORGANIZATION 은 아직 어제 확정치를 준다 -- 서로 다른 날짜가 한 시트에
    들어가는 것이 제일 나쁜 결과다.
    """
    if len(rows) < ROWS:
        raise NotSettled(f"{label}: {ROWS}종목이 안 됨 ({len(rows)}개)")
    top = rows[0]
    if top.get("bizdateFrom") != today:
        raise NotSettled(
            f"{label}: 데이터 날짜가 오늘이 아님 "
            f"(응답 {top.get('bizdateFrom')}, 오늘 {today}). 휴장일이거나 확정 전.")
    if top.get("estimated"):
        raise NotSettled(f"{label}: 장중 추정치(estimated=true)")
    if int(top.get("accTradeAmount") or 0) <= 0:
        raise NotSettled(f"{label}: 금액이 0. 확정 전 데이터.")


def to_pairs(rows: list[dict]) -> list[list]:
    """종목명 / 금액(백만원) 20행."""
    return [[r["itemname"], round(int(r["accTradeAmount"]) / 1e6)]
            for r in rows[:ROWS]]


def item_code(row: dict) -> str | None:
    """API 마다 종목코드 키가 제각각이라 한 군데서 흡수한다."""
    for key in ("itemcode", "itemCode", "code", "stockCode"):
        value = row.get(key)
        if value:
            return str(value).zfill(6)
    return None


def merge_blocks(raw: dict) -> list[dict]:
    """네 블록을 종목별로 합친다. 순매수액 내림차순, 단위는 시트와 같은 백만원.

    외인과 기관이 같은 종목을 사면 두 번 세지 않고 더한다 -- 봇이 묻는 것은
    '며칠 순매수 상위에 들었나'이지 '몇 블록에 들었나'가 아니다. 어느 블록에서
    왔는지는 blocks 에 남겨 두어 나중에 되짚을 수 있게 한다.
    """
    acc: dict[str, dict] = {}
    for (tab, market), rows in sorted(raw.items()):
        for r in rows[:ROWS]:
            code = item_code(r)
            if not code:
                continue
            entry = acc.setdefault(code, {"code": code,
                                          "name": r.get("itemname") or code,
                                          "amount": 0, "blocks": []})
            entry["amount"] += round(int(r["accTradeAmount"]) / 1e6)
            entry["blocks"].append(f"{tab}/{market}")
    return sorted(acc.values(), key=lambda e: -e["amount"])


def consensus_window(windows: dict[str, list[str]]) -> list[str]:
    """네 블록의 날짜 줄은 같아야 한다. 갈리면 다수를 쓰고 갈렸다고 알린다.

    갈리는 것은 사람이 한 탭만 손으로 밀었거나 지난 실행이 중간에 죽은 경우다.
    어느 쪽이든 조용히 한쪽을 고르면 안 되지만, 그렇다고 그날 수급 신호를 통째로
    끄는 것도 과하다 -- 다수결로 가되 로그에 남긴다.
    """
    if not windows:
        return []
    counts: dict[tuple, int] = {}
    for labels in windows.values():
        counts[tuple(labels)] = counts.get(tuple(labels), 0) + 1
    best, votes = max(counts.items(), key=lambda kv: kv[1])
    if len(counts) > 1:
        print(f"  [주의] 블록마다 날짜 줄이 다릅니다 ({len(counts)}종). "
              f"{votes}/{len(windows)} 인 [{' '.join(x or '-' for x in best)}] 를 씁니다.",
              file=sys.stderr)
    return [x for x in best if x]


def export_payload(raw: dict, windows: dict[str, list[str]], now: datetime) -> dict:
    """방금 시트에 쓴 것을 봇이 읽을 한 덩이로. 시트에 없는 것은 종목코드뿐이다."""
    return {
        "date": now.strftime("%Y%m%d"),
        "label": f"{now.month}/{now.day}",
        "window_labels": consensus_window(windows),
        "generated_at": now.isoformat(timespec="seconds"),
        "stocks": merge_blocks(raw),
    }


def plan_slots(labels: list[str], today_label: str) -> tuple[int, bool]:
    """오늘 데이터를 어느 칸에 쓸지, 그리고 왼쪽으로 밀어야 하는지.

    - 오늘 날짜가 이미 있으면 그 칸을 덮어쓴다. 재실행이 창을 한 칸 더 밀어
      멀쩡한 날을 버리는 것을 막는 유일한 가드다.
    - 빈 칸이 있으면 가장 왼쪽 빈 칸에 넣는다(창이 아직 안 찼다).
    - 다 찼으면 한 칸 밀고 마지막 칸에 넣는다.
    """
    if today_label in labels:
        return labels.index(today_label), False
    for i, v in enumerate(labels):
        if not v:
            return i, False
    return len(labels) - 1, True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="수집·검증·계획만 하고 시트는 건드리지 않는다")
    ap.add_argument("--export", metavar="PATH",
                    help="시트에 쓴 오늘치를 종목코드까지 붙여 이 파일로 내보낸다")
    a = ap.parse_args()

    now = datetime.now(KST)
    if now.weekday() > 4:
        print(f"[건너뜀] 주말({now:%A})")
        return 0
    today_key = now.strftime("%Y%m%d")
    today_label = f"{now.month}/{now.day}"
    print(f"{now:%Y-%m-%d %H:%M} KST — 오늘 {today_label}"
          + (" [dry-run]" if a.dry_run else ""))

    # 네 블록을 모두 받아 검증한 뒤에야 쓰기 시작한다. 하나라도 확정 전이면
    # 시트를 반만 채우는 것보다 아무것도 안 쓰는 편이 낫다.
    fresh, raw = {}, {}
    try:
        for tab, cfg in TABS.items():
            for market in MARKETS:
                label = f"{tab}/{market}"
                rows = fetch(cfg["investor"], market)
                validate(rows, today_key, label)
                fresh[(tab, market)] = to_pairs(rows)
                raw[(tab, market)] = rows[:ROWS]
                print(f"  {label}: 1위 {rows[0]['itemname']} "
                      f"{round(int(rows[0]['accTradeAmount']) / 1e6):,}백만")
    except NotSettled as exc:
        # 휴장일과 확정 전은 고장이 아니다. 실패로 끝내면 매 휴일마다 빨간
        # 실행이 쌓여서 진짜 고장을 가린다.
        print(f"[건너뜀] {exc}")
        return 0

    book = Spreadsheet(SPREADSHEET_ID)
    writes: list[tuple[int, str, list[list]]] = []
    windows: dict[str, list[str]] = {}

    for tab, cfg in TABS.items():
        gid = cfg["gid"]
        for market, m in MARKETS.items():
            drow, frow = m["date_row"], m["first_row"]
            last = frow + ROWS - 1

            # 표시값은 오늘이 이미 있는지 비교하는 데만 쓰고, 실제로 옮겨 적는
            # 것은 원시값이다. 날짜 칸은 날짜값(serial)이라 표시문자열 "9/1"
            # 을 되쓰면 날짜가 텍스트로 바뀌어 서식이 깨진다.
            shown = book.read_formatted(gid, f"B{drow}:K{drow}")
            shown = ((shown[0] if shown else []) + [""] * WIDTH)[:WIDTH]
            stored = book.read(gid, f"B{drow}:K{drow}")
            stored = ((stored[0] if stored else []) + [""] * WIDTH)[:WIDTH]
            labels = [str(shown[i * 2]).strip() for i in range(len(SLOTS))]
            dates = [stored[i * 2] for i in range(len(SLOTS))]

            grid = book.read(gid, f"B{frow}:K{last}")
            grid = [(list(r) + [""] * WIDTH)[:WIDTH] for r in grid]
            grid += [[""] * WIDTH for _ in range(ROWS - len(grid))]

            idx, shift = plan_slots(labels, today_label)
            dropped = labels[0] if shift else None
            if shift:
                labels = labels[1:] + [""]
                dates = dates[1:] + [""]
                grid = [r[2:] + ["", ""] for r in grid]

            labels[idx] = today_label
            dates[idx] = now.strftime("%Y-%m-%d")   # USER_ENTERED 가 날짜로 해석
            pairs = fresh[(tab, market)]
            for r in range(ROWS):
                grid[r][idx * 2:idx * 2 + 2] = pairs[r]

            date_cells = [[]]
            for d in dates:
                date_cells[0] += [d if d != "" else "", ""]
            date_cells[0] = date_cells[0][:WIDTH]

            windows[f"{tab}/{market}"] = list(labels)
            how = (f"한 칸 밀어냄({dropped} 버림)" if shift
                   else "이미 있던 오늘 칸 덮어씀" if today_label in labels[:idx] + labels[idx + 1:]
                   else "빈 칸에 추가")
            print(f"  {tab}/{market}: {SLOTS[idx]}{frow} — {how}"
                  f"  [{' '.join(x or '-' for x in labels)}]")
            writes.append((gid, f"B{drow}:K{drow}", date_cells))
            writes.append((gid, f"B{frow}:K{last}", grid))

    if a.dry_run:
        for gid, rng, _ in writes:
            print(f"  (dry-run) gid={gid} {rng}")
        if a.export:
            # 내보내기는 '시트에 이렇게 적혀 있다'는 말이다. 쓰지 않은 날에
            # 그 말을 파일로 남기면, 봇은 있지도 않은 칸을 근거로 신호를 켠다.
            print("  (dry-run) 내보내기도 건너뜁니다 — 시트를 안 썼으므로")
        print("dry-run 종료 — 시트를 건드리지 않았습니다")
        return 0

    for gid, rng, values in writes:
        n = book.write(gid, rng, values)
        print(f"  기록 gid={gid} {rng} ({n}칸)")

    if a.export:
        payload = export_payload(raw, windows, now)
        with open(a.export, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"  내보냄 {a.export} — {len(payload['stocks'])}종목, "
              f"창 [{' '.join(payload['window_labels'])}]")
    print("완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
