#!/usr/bin/env python3
"""시트에 적힌 것을 tradesignal 이 읽을 수 있는 JSON 으로 내보낸다.

    python feed_export.py themes --feed-dir /tmp/theme_bot/feed
    python feed_export.py supply --today supply_today.json --feed-dir /tmp/theme_bot/feed
    python feed_export.py codes

## 왜 이런 것이 필요한가

아침 매수 후보 봇(volume-surge-telegram-bot/tradesignal)의 테마·수급 신호를
**이 시트에 적힌 내용**으로 돌리기로 했다. 그 전에는 봇이 네이버 테마 목록
API 를 따로 읽었는데, 문턱이 여기와 달라서(테마 등락률 1% 이상, 시총 문턱
없음) 시트에 없는 테마가 추천에 올라왔다 -- 2026-09-15 아침의 '여행'이 그
예다. 시트가 유일한 기준이면 그런 어긋남이 생길 수 없다.

봇은 다른 저장소에 있고 구글 인증이 없다. 대신 카드가 이미 공개 저장소
theme_bot 으로 나가고 있으므로, 시트 내용도 같은 길로 내보내 봇이
raw.githubusercontent.com 으로 받아 가게 한다.

## 종목코드는 왜 따로 붙이는가

시트에는 종목코드가 없다 -- 사람이 읽는 표라 종목명·상승률·거래대금뿐이다.
그런데 봇은 일봉(거래량 배수·이동평균·신고가)을 받아야 하고 그러려면 코드가
있어야 한다. 그래서 스캔이 알고 있는 이름->코드를 codes.json 에 쌓아 두고,
내보낼 때 이름으로 붙인다. 스캔이 0개인 날(시트에서 복원한 날)에도 어제까지
쌓인 지도가 있으면 코드가 붙는다.

코드를 못 찾은 종목은 버리지 않고 code:"" 로 내보내고 missing 에 이름을
남긴다. 조용히 사라지면 봇 쪽에서 '오늘은 그 종목이 시트에 없었다'와
구별되지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
CODE_FILE = Path(__file__).with_name("codes.json")

# 수급 시트가 들고 있는 날짜 칸 수. 봇의 '최근 5거래일 중 3일' 과 같은 창이다.
SUPPLY_WINDOW = 5


# ---------------------------------------------------------------- 이름->코드

def load_code_map(path: Path | None = None) -> dict[str, str]:
    """codes.json 을 {종목명: 코드} 로. 없거나 깨져 있으면 빈 지도.

    지도가 없는 것은 고장이 아니다 -- 첫 실행에는 없고, 그날은 스캔이 들고 온
    코드만으로 내보낸다.
    """
    path = path or CODE_FILE
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[WARN] {path.name} 을 읽지 못했습니다: {exc}", file=sys.stderr)
        return {}
    codes = data.get("codes") if isinstance(data, dict) else data
    return {str(k): str(v).zfill(6) for k, v in (codes or {}).items() if k and v}


def save_code_map(mapping: dict[str, str], path: Path | None = None) -> None:
    path = path or CODE_FILE
    body = {
        "_": ("스캔이 본 종목의 이름->코드. 시트에는 코드가 없어서, 시트 내용을 "
              "tradesignal 로 내보낼 때 여기서 붙인다. feed_export.py codes 가 "
              "매일 덧붙이며 지우지는 않는다 -- 오늘 안 오른 종목도 어제 시트에는 "
              "있을 수 있기 때문이다."),
        "codes": dict(sorted(mapping.items())),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)
        f.write("\n")


def codes_from_naver() -> dict[str, str]:
    """네이버 테마 구성종목을 훑어 이름->코드를 통째로 긁는다. 요청이 많다.

    평소에는 쓸 일이 없다. 매일의 스캔이 raw_scan.json 에 남기는 조건 충족
    종목이 곧 시트에 오를 수 있는 것의 전부라, 그쪽에서 공짜로 얻기 때문이다.

    이것이 필요한 때는 지도가 아직 없을 때다 -- 내보내기를 붙이기 전에 돈
    날의 피드를 다시 만들면, 그날 스캔 결과가 작업 폴더에 없고 codes.json 도
    비어 있어서 시트의 종목 전부가 코드 없이 나간다. 2026-09-15 이 그랬다.

    조건 충족 여부를 보지 않고 구성종목을 전부 담는다. 지도는 오늘 오른
    종목만이 아니라 언젠가 시트에 오를 수 있는 이름을 알고 있어야 한다.
    """
    import theme_scan

    out: dict[str, str] = {}
    themes = theme_scan.fetch_theme_list()
    print(f"  테마 {len(themes)}개에서 구성종목을 훑습니다 (요청이 그만큼 나갑니다)",
          file=sys.stderr)
    for theme in themes:
        try:
            stocks = theme_scan.fetch_theme_stocks(theme["no"])
        except Exception as exc:
            print(f"  [건너뜀] 테마 {theme.get('name') or theme['no']}: {exc}",
                  file=sys.stderr)
            continue
        for stock in stocks:
            name, code = str(stock.get("name") or ""), str(stock.get("code") or "")
            if name and code:
                out[name] = code.zfill(6)
    return out


def codes_from_scan(raw_scan: list[dict], groups: list[dict]) -> dict[str, str]:
    """오늘 스캔이 본 이름->코드. 두 파일 모두에서 긁는다.

    raw_scan 은 조건을 넘은 종목 전부(시트에 오를 수 있는 것의 상위집합)이고,
    final_groups 는 그중 실제로 시트에 간 것이다. 시트에서 복원한 날의
    final_groups 는 코드가 비어 있으므로 여기서 아무것도 얻지 못하는데, 그날은
    raw_scan 도 비어 있는 날이라 어차피 쌓인 지도로 붙인다.
    """
    out: dict[str, str] = {}
    for theme in raw_scan or []:
        for s in theme.get("qualifying") or []:
            name, code = str(s.get("name") or ""), str(s.get("code") or "")
            if name and code:
                out[name] = code.zfill(6)
    for g in groups or []:
        for s in (g.get("stocks") or g.get("top5") or []):
            name, code = str(s.get("name") or ""), str(s.get("code") or "")
            if name and code:
                out[name] = code.zfill(6)
    return out


# ---------------------------------------------------------------- 테마 피드

def themes_feed(groups: list[dict], code_map: dict[str, str], day: str,
                label: str, generated_at: str) -> dict:
    """시트 블록 -> 봇이 읽는 테마 피드.

    거래대금 단위는 **바꾸지 않는다**. 시트 칸도 final_groups.json 도 백만원이고
    (groups_from_sheet 의 주석 참고), 여기서 억으로 고쳐 내보내면 받는 쪽이
    다시 100 을 곱해야 하는지 아닌지를 두고 또 한 번 틀린다.
    """
    out, missing = [], []
    for g in groups:
        stocks = []
        for s in (g.get("stocks") or g.get("top5") or []):
            name = str(s.get("name") or "").strip()
            if not name:
                continue
            code = str(s.get("code") or "") or code_map.get(name, "")
            if not code:
                missing.append(name)
            stocks.append({
                "name": name,
                "code": code.zfill(6) if code else "",
                "rate": s.get("rate"),
                "amount": s.get("amount"),
            })
        if stocks:
            out.append({"keyword": g.get("keyword") or "", "stocks": stocks})
    return {
        "date": day,
        "label": label,
        "source": "daily-sheet",
        "generated_at": generated_at,
        "groups": out,
        "missing_codes": sorted(set(missing)),
    }


# ---------------------------------------------------------------- 수급 피드

def merge_supply_window(previous: dict | None, today: dict | None,
                        labels: list[str]) -> list[dict]:
    """어제까지의 창에 오늘을 얹고, 시트에 남아 있는 날짜만 남긴다.

    시트가 날짜 다섯 칸을 왼쪽으로 미는 창이므로 피드도 같은 창이어야 한다.
    `labels` 는 그 시트에서 방금 읽은 날짜 줄이다 -- 우리 파일이 아니라 시트가
    창의 주인이라, 사람이 시트를 손으로 밀었어도 다음 실행에 따라간다.

    같은 날짜가 두 번 오면 오늘 것이 이긴다(재실행).
    """
    days = {}
    for day in (previous or {}).get("days") or []:
        if day.get("label"):
            days[day["label"]] = day
    if today and today.get("label"):
        days[today["label"]] = today
    return [days[label] for label in labels if label in days]


def supply_feed(previous: dict | None, today: dict, generated_at: str) -> dict:
    labels = list(today.get("window_labels") or [])
    if not labels:
        # 시트에서 날짜 줄을 못 읽었으면 오늘 하루만이라도 남긴다. 창을 통째로
        # 버리면 봇의 수급 신호가 꺼진 채 하루가 가는데, 그보다는 낫다.
        labels = [today.get("label", "")]
    days = merge_supply_window(previous, today, labels)
    return {
        "date": today.get("date", ""),
        "window": labels,
        "source": "supply-sheet",
        "generated_at": generated_at,
        "days": days,
    }


# ---------------------------------------------------------------- 파일 쓰기

def write_feed(feed_dir: Path, kind: str, payload: dict) -> list[Path]:
    """<feed>/<kind>/<날짜>.json 과 latest.json 을 같이 쓴다.

    봇은 latest.json 만 읽는다. 날짜별 파일은 나중에 '그날 무엇을 보고 골랐나'
    를 되짚기 위한 것이다 -- 시그널 원장과 같은 이유다.
    """
    target = feed_dir / kind
    target.mkdir(parents=True, exist_ok=True)
    written = []
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    for name in (f"{payload.get('date') or 'unknown'}.json", "latest.json"):
        path = target / name
        path.write_text(body, encoding="utf-8")
        written.append(path)
    return written


def read_json(path: Path | str, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[WARN] {path} 을 읽지 못했습니다: {exc}", file=sys.stderr)
        return default


# ---------------------------------------------------------------- 명령

def cmd_codes(args) -> int:
    mapping = load_code_map()
    before = len(mapping)
    if args.from_naver:
        mapping.update(codes_from_naver())
    else:
        mapping.update(codes_from_scan(read_json(args.scan, []) or [],
                                       read_json(args.groups, []) or []))
    print(f"codes.json: {before} -> {len(mapping)}종목 (+{len(mapping) - before})")
    if args.dry_run:
        print("dry-run — 파일을 쓰지 않았습니다")
        return 0
    save_code_map(mapping)
    return 0


def cmd_themes(args) -> int:
    from as_of import as_of
    from groups_from_sheet import DAILY_ID, SEARCH_ROWS, groups_from_grid
    from sheets import Spreadsheet
    from theme_write import resolve_daily_gid

    now, overridden = as_of()
    label = args.date or f"{now.month}/{now.day}"
    day = now.strftime("%Y%m%d")
    if overridden:
        print(f"THEME_AS_OF={now:%Y-%m-%d} — {label} 블록을 내보냅니다")

    book = Spreadsheet(DAILY_ID)
    grid = book.read_formatted(resolve_daily_gid(book, now), f"A1:AN{SEARCH_ROWS}")
    groups = groups_from_grid(grid, label)
    if not groups:
        # 빈 피드를 올리면 봇은 '오늘 테마가 하나도 없었다'로 읽는다. 그건
        # 시트를 못 읽은 것과 전혀 다른 말이다 -- 어제 피드를 그대로 두고 죽는다.
        print(f"[중단] Daily 시트에서 {label} 블록을 찾지 못했거나 비어 있습니다. "
              "피드를 갱신하지 않습니다.", file=sys.stderr)
        return 1

    code_map = load_code_map()
    code_map.update(codes_from_scan(read_json("raw_scan.json", []) or [],
                                    read_json("final_groups.json", []) or []))
    payload = themes_feed(groups, code_map, day, label,
                          datetime.now(KST).isoformat(timespec="seconds"))

    total = sum(len(g["stocks"]) for g in payload["groups"])
    print(f"{label} 블록 {len(payload['groups'])}개 그룹 / {total}종목")
    for g in payload["groups"]:
        names = ", ".join(s["name"] for s in g["stocks"][:3])
        print(f"  [{g['keyword']}] {len(g['stocks'])}종목 — {names}")
    if payload["missing_codes"]:
        print(f"  [주의] 코드를 못 붙인 종목 {len(payload['missing_codes'])}개: "
              f"{', '.join(payload['missing_codes'])}", file=sys.stderr)

    if args.dry_run:
        print("dry-run — 파일을 쓰지 않았습니다")
        return 0
    for path in write_feed(Path(args.feed_dir), "themes", payload):
        print(f"  기록 {path}")
    return 0


def cmd_supply(args) -> int:
    today = read_json(args.today)
    if not today or not today.get("stocks"):
        print(f"[건너뜀] {args.today} 에 내보낼 것이 없습니다 (휴장이거나 확정 전)")
        return 0

    feed_dir = Path(args.feed_dir)
    previous = read_json(feed_dir / "supply" / "latest.json")
    payload = supply_feed(previous, today, datetime.now(KST).isoformat(timespec="seconds"))

    kept = " ".join(d["label"] for d in payload["days"])
    print(f"수급 창 {len(payload['days'])}/{SUPPLY_WINDOW}일 [{kept}] — "
          f"오늘({today.get('label')}) {len(today['stocks'])}종목")
    if len(payload["days"]) < SUPPLY_WINDOW:
        print(f"  (창이 아직 안 찼습니다 — 봇의 수급 신호는 {SUPPLY_WINDOW}일이 "
              "차야 켜집니다)")

    if args.dry_run:
        print("dry-run — 파일을 쓰지 않았습니다")
        return 0
    for path in write_feed(feed_dir, "supply", payload):
        print(f"  기록 {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    # --dry-run 은 하위 명령마다 단다. 위에 한 번만 달면 하위 명령 뒤에 적은
    # --dry-run 이 조용히 무시되는데, 그 실수는 파일을 쓰고 나서야 드러난다.
    def dry(parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="출력만 하고 파일은 쓰지 않는다")

    p = sub.add_parser("themes", help="Daily 시트의 오늘 블록을 피드로")
    dry(p)
    p.add_argument("--feed-dir", default="feed")
    p.add_argument("--date", help="M/D 를 직접 지정 (기본: 오늘 또는 THEME_AS_OF)")
    p.set_defaults(func=cmd_themes)

    p = sub.add_parser("supply", help="수급 시트의 5일 창을 피드로")
    dry(p)
    p.add_argument("--today", default="supply_today.json",
                   help="supply_demand.py --export 가 쓴 오늘치")
    p.add_argument("--feed-dir", default="feed")
    p.set_defaults(func=cmd_supply)

    p = sub.add_parser("codes", help="스캔이 본 이름->코드를 codes.json 에 쌓는다")
    dry(p)
    p.add_argument("--scan", default="raw_scan.json")
    p.add_argument("--groups", default="final_groups.json")
    p.add_argument("--from-naver", action="store_true",
                   help="스캔 결과 대신 네이버 구성종목을 통째로 훑는다 "
                        "(지도가 비어 있을 때. 요청이 수백 건 나간다)")
    p.set_defaults(func=cmd_codes)

    args = ap.parse_args()
    if not hasattr(args, "dry_run"):
        args.dry_run = False
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
