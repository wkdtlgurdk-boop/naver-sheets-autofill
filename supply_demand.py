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

네이버에는 일별 과거 조회가 없다. 그래서 하루를 놓치면 API 로는 영영 못
받는다. 그 하루는 사람이 HTS 에서 보고 적어 넣는다:

    python supply_demand.py --backfill backfill/20260914.txt --day 2026-09-14

--export 는 방금 시트에 쓴 것을 종목코드까지 붙여 한 파일로 남긴다.
feed_export.py 가 그것을 공개 저장소의 5일 창에 얹고, 아침 매수 후보
봇(volume-surge-telegram-bot/tradesignal)이 거기서 수급 신호를 센다. 시트에는
종목코드가 없어서, 코드를 아는 유일한 자리가 방금 받은 이 응답이다.

    python supply_demand.py --export supply_today.json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
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
    """오늘 확정치가 아직 아니다. 오류가 아니라 '아직'이다.

    `trading_day` 는 오늘이 장이 선 날인지를 가른다. True 면 오늘 자료가
    오긴 왔는데 아직 안 굳은 것이고(늦게 돌면 해결된다), False 면 애초에
    오늘 자료가 없는 것이다(휴장일 -- 기다려도 안 나온다). 마지막 실행에서
    둘을 같이 취급하면 공휴일마다 빨간 실행이 쌓여 진짜 고장을 가린다.
    """

    def __init__(self, message: str, trading_day: bool = True):
        super().__init__(message)
        self.trading_day = trading_day


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
        # 오늘 자료가 아예 없다 = 휴장일로 본다. 늦게 다시 돌아도 안 바뀐다.
        raise NotSettled(
            f"{label}: 데이터 날짜가 오늘이 아님 "
            f"(응답 {top.get('bizdateFrom')}, 오늘 {today}). 휴장일이거나 확정 전.",
            trading_day=False)
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


BLOCK_ORDER = [("외인", "KOSPI"), ("외인", "KOSDAQ"),
               ("기관", "KOSPI"), ("기관", "KOSDAQ")]


def parse_backfill(text: str) -> dict[tuple[str, str], list[list]]:
    """손으로 옮겨 적은 하루치를 읽는다.

    API 가 못 주는 날을 사람이 HTS 에서 보고 채우는 통로다. 9/14 가 그랬다 --
    네이버 수급동향에는 일별 과거 조회가 없어서, 그날을 놓치면 어디서도 다시
    받을 수 없다.

    형식은 눈으로 확인 가능한 최소한으로 둔다. 블록 머리글 한 줄, 그 아래
    스무 줄, 각 줄은 '종목명 금액' 이고 금액 단위는 **백만원** -- 시트에
    들어가는 단위와 같다. 단위를 맞추는 것은 사람 몫이고, 여기서는 스무 줄이
    다 있는지와 숫자가 숫자인지만 본다.

        # 외인/KOSPI
        KODEX 200   61942
        ...

    종목명에 공백이 있으므로(KODEX 200, 삼성전자우) 금액은 **마지막 토큰**으로
    자른다.
    """
    blocks: dict[tuple[str, str], list[list]] = {}
    key: tuple[str, str] | None = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            head = line.lstrip("#").strip()
            tab, _, market = head.partition("/")
            key = (tab.strip(), market.strip())
            if key not in BLOCK_ORDER:
                raise SystemExit(f"[중단] {lineno}행: 모르는 블록 {head!r}. "
                                 f"{BLOCK_ORDER} 중 하나여야 합니다.")
            if key in blocks:
                raise SystemExit(f"[중단] {lineno}행: {head} 가 두 번 나옵니다.")
            blocks[key] = []
            continue
        if key is None:
            raise SystemExit(f"[중단] {lineno}행: 블록 머리글(#) 보다 먼저 나온 줄입니다.")
        name, _, amount = line.rpartition(" ")
        name, amount = name.strip(), amount.replace(",", "").strip()
        try:
            value = int(round(float(amount)))
        except ValueError:
            raise SystemExit(f"[중단] {lineno}행: 금액이 숫자가 아닙니다 -- {line!r}") from None
        if not name:
            raise SystemExit(f"[중단] {lineno}행: 종목명이 비었습니다 -- {line!r}")
        blocks[key].append([name, value])

    missing = [f"{t}/{m}" for t, m in BLOCK_ORDER if (t, m) not in blocks]
    if missing:
        raise SystemExit(f"[중단] 빠진 블록: {', '.join(missing)}")
    for (tab, market), rows in blocks.items():
        if len(rows) != ROWS:
            raise SystemExit(f"[중단] {tab}/{market}: {ROWS}종목이어야 하는데 "
                             f"{len(rows)}개입니다.")
        # 순매수 상위 목록은 금액 내림차순이다. 어긋나면 옮겨 적다가 줄을
        # 건너뛰거나 섞은 것이므로, 그대로 넣지 않고 세운다.
        amounts = [r[1] for r in rows]
        for i in range(1, ROWS):
            if amounts[i] > amounts[i - 1]:
                raise SystemExit(
                    f"[중단] {tab}/{market}: {i + 1}번째가 {i}번째보다 큽니다 "
                    f"({rows[i - 1][0]} {amounts[i - 1]:,} < "
                    f"{rows[i][0]} {amounts[i]:,}). 순매수 상위는 금액 "
                    f"내림차순이어야 합니다 — 줄을 건너뛰었는지 보세요.")
    return blocks


def write_day(fresh: dict, day: datetime, label: str,
              dry_run: bool) -> tuple[int, dict[str, list[str]]]:
    """(종료코드, 블록별 날짜 줄). 날짜 줄은 --export 가 창을 맞추는 데 쓴다."""
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

            idx, shift = plan_slots(labels, label)
            dropped = labels[0] if shift else None
            # labels 를 고치기 '전에' 잡아 둔다. 고친 뒤에 찾으면 그 칸은 이미
            # 오늘 날짜라 덮어쓴 것인지 빈 칸이었는지 구별할 수 없다.
            overwrote = labels[idx] == label
            if shift:
                labels = labels[1:] + [""]
                dates = dates[1:] + [""]
                grid = [r[2:] + ["", ""] for r in grid]

            labels[idx] = label
            dates[idx] = day.strftime("%Y-%m-%d")   # USER_ENTERED 가 날짜로 해석
            pairs = fresh[(tab, market)]
            for r in range(ROWS):
                grid[r][idx * 2:idx * 2 + 2] = pairs[r]

            date_cells = [[]]
            for d in dates:
                date_cells[0] += [d if d != "" else "", ""]
            date_cells[0] = date_cells[0][:WIDTH]

            windows[f"{tab}/{market}"] = list(labels)
            how = (f"한 칸 밀어냄({dropped} 버림)" if shift
                   else "이미 있던 칸 덮어씀" if overwrote
                   else "빈 칸에 추가")
            print(f"  {tab}/{market}: {SLOTS[idx]}{frow} — {how}"
                  f"  [{' '.join(x or '-' for x in labels)}]")
            writes.append((gid, f"B{drow}:K{drow}", date_cells))
            writes.append((gid, f"B{frow}:K{last}", grid))

    if dry_run:
        for gid, rng, _ in writes:
            print(f"  (dry-run) gid={gid} {rng}")
        print("dry-run 종료 — 시트를 건드리지 않았습니다")
        return 0, windows

    for gid, rng, values in writes:
        n = book.write(gid, rng, values)
        print(f"  기록 gid={gid} {rng} ({n}칸)")
    print("완료")
    return 0, windows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="수집·검증·계획만 하고 시트는 건드리지 않는다")
    ap.add_argument("--backfill", metavar="FILE",
                    help="API 대신 손으로 적은 파일에서 하루치를 읽는다 "
                         "(형식은 parse_backfill 참고). --day 와 함께 쓴다")
    ap.add_argument("--day", metavar="YYYY-MM-DD",
                    help="--backfill 이 채울 날짜")
    ap.add_argument("--export", metavar="PATH",
                    help="시트에 쓴 오늘치를 종목코드까지 붙여 이 파일로 내보낸다")
    a = ap.parse_args()

    if bool(a.backfill) != bool(a.day):
        raise SystemExit("[중단] --backfill 과 --day 는 같이 써야 합니다.")

    if a.backfill:
        # 지난 날짜를 명시적으로 지목한 것이므로 주말 가드도 확정치 검증도
        # 지나간다. 사람이 보고 적은 값에 '확정 전' 이라는 개념이 없다.
        day = datetime.strptime(a.day, "%Y-%m-%d").replace(tzinfo=KST)
        fresh = parse_backfill(pathlib.Path(a.backfill).read_text(encoding="utf-8"))
        label = f"{day.month}/{day.day}"
        print(f"{a.day} 손으로 채움 — {label}" + (" [dry-run]" if a.dry_run else ""))
        for tab, market in BLOCK_ORDER:
            top = fresh[(tab, market)][0]
            print(f"  {tab}/{market}: 1위 {top[0]} {top[1]:,}백만")
        rc, _ = write_day(fresh, day, label, a.dry_run)
        if a.export:
            # 손으로 적은 파일에는 종목코드가 없다. 봇은 코드로 종목을 세므로
            # 내보낼 것이 없다 -- 그 하루는 시트에만 남고 피드의 창에서는
            # 빠진다(순매수 일수를 적게 셀 뿐, 없는 날을 지어내지는 않는다).
            print("  [알림] 손으로 채운 날은 종목코드가 없어 피드로 내보내지 않습니다.")
        return rc

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
        # 다만 그날의 마지막 실행까지 건너뛰면 그 하루는 영영 빈 채로 남는다.
        # 2026-09-14 가 그랬다 -- 20:20 에 estimated=true 라 건너뛰었고, 초록불
        # 이라 아무도 몰랐다가 다음 날 사람이 발견했다. 네이버에는 일별 과거
        # 조회가 없어서 그날은 --backfill 로 손으로 넣는 수밖에 없다.
        if os.environ.get("SD_STRICT") and exc.trading_day:
            print("[중단] 마지막 실행인데 아직 확정 전입니다 — 오늘치가 빠집니다. "
                  "확정 시각이 더 밀렸는지 보고 cron 을 조정하세요.", file=sys.stderr)
            return 1
        return 0

    rc, windows = write_day(fresh, now, today_label, a.dry_run)
    if rc == 0 and a.export:
        if a.dry_run:
            # 내보내기는 '시트에 이렇게 적혀 있다'는 말이다. 쓰지 않은 날에
            # 그 말을 파일로 남기면, 봇은 있지도 않은 칸을 근거로 신호를 켠다.
            print("  (dry-run) 내보내기도 건너뜁니다 — 시트를 안 썼으므로")
        else:
            payload = export_payload(raw, windows, now)
            with open(a.export, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"  내보냄 {a.export} — {len(payload['stocks'])}종목, "
                  f"창 [{' '.join(payload['window_labels'])}]")
    return rc


if __name__ == "__main__":
    sys.exit(main())
