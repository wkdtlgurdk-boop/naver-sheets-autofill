#!/usr/bin/env python3
"""오늘의 수급: 외국인·기관 순매수 상위 5종목씩을 supply_groups.json 으로 남긴다.

테마와 따로 매일 올린다(10/15 사용자 지정). supply-demand 워크플로가 수급 시트를
확정치로 쓴 뒤에 부른다 -- 그래서 여기서도 확정치가 아니면 멈춘다
(theme_scan.investor_groups). 뒤이어 cards.py --supply, post_threads.py --supply,
shorts.py --supply, youtube_upload.py --supply 가 이 파일을 읽는다.

    python supply_top.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import theme_scan as ts
from as_of import as_of

SUPPLY_FILE = "supply_groups.json"


def load_supply(here: Path) -> list[dict]:
    path = Path(here) / SUPPLY_FILE
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else []


def main() -> int:
    now, _ = as_of()
    groups = ts.investor_groups(f"{now:%Y%m%d}")
    out = Path(__file__).parent / SUPPLY_FILE
    out.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{now:%Y-%m-%d} 오늘의 수급 {len(groups)}칸 → {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
