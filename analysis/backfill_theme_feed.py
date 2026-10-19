#!/usr/bin/env python3
"""theme_bot/feed/themes/ 에 날짜별 피드가 없는 지난 날을 시트 덤프로 메운다.

    python analysis/backfill_theme_feed.py --feed ../theme_bot/feed/themes          # 쓰기
    python analysis/backfill_theme_feed.py --feed ../theme_bot/feed/themes --dry-run

날짜별 피드는 2026-09-15 부터 매일 쌓였다. 아침 매수 후보 봇의 '테마 재부각'
규칙(tradesignal/theme_revival.py)은 그 종목이 **최근 60거래일**에 몇 번 실렸는지를
봐야 해서, 그 앞을 analysis/sheet_days.json(theme-stats.yml dump_only)으로 채운다.

- **이미 있는 날은 건드리지 않는다.** 그날 봇이 실제로 본 기록이다.
- 장이 선 날만 쓴다. 장날은 네이버 코스피 일봉 날짜다(analysis/cache/bars.json,
  theme_horizon.py 가 받아 둔 것). 추석처럼 휴장일에 적힌 블록은 버린다.
- source 는 'sheet-backfill'. 시트에는 거래대금이 있지만 덤프에 없어 amount 는 비운다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from feed_export import load_code_map, themes_feed  # noqa: E402

KST = timezone(timedelta(hours=9))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", required=True, help="theme_bot/feed/themes 경로")
    ap.add_argument("--sheet", default=str(HERE / "sheet_days.json"))
    ap.add_argument("--bars", default=str(HERE / "cache" / "bars.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    days = json.loads(Path(args.sheet).read_text(encoding="utf-8"))
    opened = {b["d"] for b in json.loads(Path(args.bars).read_text())["index"]["KOSPI"]}
    code_map = load_code_map()
    feed = Path(args.feed)
    now = datetime.now(KST).isoformat(timespec="seconds")

    wrote, kept, closed = [], 0, []
    for iso, groups in sorted(days.items()):
        if iso not in opened:
            closed.append(iso)
            continue
        day = iso.replace("-", "")
        path = feed / f"{day}.json"
        if path.exists():
            kept += 1
            continue
        doc = themes_feed(groups, code_map, day, f"{int(iso[5:7])}/{int(iso[8:])}", now)
        doc["source"] = "sheet-backfill"
        if not args.dry_run:
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        wrote.append(day)
    print(f"새로 씀 {len(wrote)}일 ({wrote[0] if wrote else '-'} ~ {wrote[-1] if wrote else '-'}), "
          f"이미 있음 {kept}일, 휴장일 블록 버림 {len(closed)}일 {closed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
