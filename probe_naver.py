#!/usr/bin/env python3
"""일회용 진단. 수급 API 응답에 무엇이 들어 있는지 러너가 찍어 본다.

supply_demand.py 는 sections.buyRankList(순매수 상위 20종목)만 읽는다. sections
가 복수형이라 그 옆에 시장 전체 순매수액이 같이 올 수 있는데, 확인한 적이 없다.

필요한 이유: 아침 알림의 수급 블록이 '상위 20종목 합계'라서 부호가 없다.
상위에서 빠진 것이 판 것인지 21등이 된 것인지 구분할 수 없어 '3일 매도 후
매수 전환' 같은 말을 못 쓴다. 시장 전체 순매수액은 부호가 있으므로 그 말을
쓸 수 있다 -- 그 값이 이 응답 안에 있는지가 이 파일이 묻는 전부다.

값은 안 찍는다. 키 이름과 자료형, 숫자 하나 정도만 본다.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

BASE = "https://stock.naver.com/api/domestic/market/trend/trendForeignOrg"
HEADERS = {
    "Referer": "https://stock.naver.com/",
    "User-Agent": "Mozilla/5.0 supply-demand-autofill/4.0",
}


def skeleton(node, depth: int = 0, limit: int = 3) -> str:
    """중첩 구조를 키와 자료형만으로 펼친다. 목록은 첫 원소만 본다."""
    pad = "  " * depth
    if isinstance(node, dict):
        out = []
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                out.append(f"{pad}{key}:")
                out.append(skeleton(value, depth + 1, limit))
            else:
                out.append(f"{pad}{key} = {value!r}"[:120])
        return "\n".join(out)
    if isinstance(node, list):
        if not node:
            return f"{pad}[] (빈 목록)"
        return f"{pad}[{len(node)}개] 첫 원소:\n" + skeleton(node[0], depth + 1, limit)
    return f"{pad}{node!r}"[:120]


def main() -> int:
    qs = urllib.parse.urlencode({
        "investorType": "FOREIGNER", "tradeType": "KRX",
        "marketType": "KOSPI", "startIdx": 0, "pageSize": 20, "periodType": "DAY",
    })
    req = urllib.request.Request(f"{BASE}?{qs}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        doc = json.load(resp)

    print("=== 최상위 키 ===")
    print(", ".join(doc.keys()))
    sections = doc.get("sections") or {}
    print("\n=== sections 키 ===")
    for key, value in sections.items():
        kind = type(value).__name__
        size = f"[{len(value)}]" if isinstance(value, (list, dict)) else ""
        print(f"  {key}: {kind}{size}")

    print("\n=== buyRankList 를 뺀 나머지 ===")
    rest = {k: v for k, v in sections.items() if k != "buyRankList"}
    print(skeleton(rest) if rest else "  (없음 -- 상위 목록이 전부다)")

    print("\n=== buyRankList 첫 원소 ===")
    rows = sections.get("buyRankList") or []
    print(skeleton(rows[0]) if rows else "  (비어 있음)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
