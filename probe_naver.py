#!/usr/bin/env python3
"""일회용 진단. 네트워크가 막힌 개발 세션 대신 러너가 네이버를 찔러 본다.

## 묻는 것

아침 알림(tradesignal)의 수급 블록을 '최근 5거래일 코스피 외국인 순매수'로
바꾸려 한다. 지금 쓰는 값은 순매수 **상위 20종목** 합계라서 부호가 없다 --
상위에서 빠진 것이 판 것인지 21등이 된 것인지 구분할 수 없어 '매도'라는
말을 쓸 수 없다.

시장 전체 순매수액은 부호가 있다. market_summary.py 가 이미 그 값을 하루치
받아 오고(/api/index/{code}/trend 의 foreignValue) Monthly 시트에 매일 쌓고
있다. 남은 질문은 하나다:

    **네이버가 그 값을 여러 날치로 한 번에 주는가?**

준다면 봇이 직접 받아 쓰면 된다 -- 시트도 피드도 안 거치고, 첫날부터 5일이
다 찬다. 안 주면 Monthly 시트(달력 격자)를 읽거나 날마다 피드에 쌓아야 하고,
후자는 5거래일이 지나야 창이 찬다.

## 값은 안 찍는다

키 이름과 자료형, 목록 길이만 본다. 숫자는 '여러 날치인가'를 가르는 데
필요한 만큼만(날짜 필드로 보이는 것) 찍는다.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://m.stock.naver.com/",
    "User-Agent": "Mozilla/5.0 supply-demand-autofill/4.0",
}

# 지수 투자자동향 후보. market_summary.py 가 쓰는 trend 가 첫째고, 나머지는
# '여러 날치'를 줄 법한 이웃 경로다. 404 는 답의 일부이므로 죽지 않고 찍는다.
INDEX_CANDIDATES = [
    # 1라운드에서 하루치 단일 객체로 확인됨. 날짜 파라미터가 먹는지 본다 --
    # 먹으면 10번 불러서 10거래일을 만든다.
    "https://m.stock.naver.com/api/index/KOSPI/trend",
    "https://m.stock.naver.com/api/index/KOSPI/trend?bizdate=20260922",
    "https://m.stock.naver.com/api/index/KOSPI/trend?pageSize=10",
    # 예전부터 있던 '투자자별 매매동향' 표. HTML 이지만 한 번에 20일쯤 준다.
    "https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate=&sosok=01&page=1",
    "https://finance.naver.com/sise/investorDealTrendDay.naver?sosok=01",
]

# 지금 수급 시트가 읽는 경로. 여기에 시장 전체 순매수액이 딸려 오는지도
# 같은 실행에서 확인해 둔다 -- 있으면 위 후보가 다 죽어도 길이 남는다.
RANK_URL = "https://stock.naver.com/api/domestic/market/trend/trendForeignOrg"
RANK_HEADERS = {
    "Referer": "https://stock.naver.com/",
    "User-Agent": "Mozilla/5.0 supply-demand-autofill/4.0",
}

# 날짜처럼 보이는 키. 이게 목록 원소마다 다르면 '여러 날치'다.
DATE_HINTS = ("date", "day", "dt", "tradedat", "localtradedat", "bizdate")


def get(url: str, headers: dict) -> tuple[int, object | None, str]:
    """(HTTP 코드, 파싱된 본문, 메모). 죽지 않는다 -- 실패도 답이다."""
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw), ""
            except json.JSONDecodeError:
                return resp.status, raw, "JSON 아님 -- HTML 로 본다"
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.reason or ""
    except Exception as exc:                       # noqa: BLE001 - 진단용
        return 0, None, f"{type(exc).__name__}: {exc}"


def looks_like_date(key: str) -> bool:
    low = key.lower()
    return any(hint in low for hint in DATE_HINTS)


def skeleton(node, depth: int = 0) -> str:
    """중첩 구조를 키와 자료형만으로 펼친다. 목록은 첫 원소만 본다."""
    pad = "  " * depth
    if isinstance(node, dict):
        out = []
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                out.append(f"{pad}{key}:")
                out.append(skeleton(value, depth + 1))
            else:
                out.append(f"{pad}{key} = {value!r}"[:120])
        return "\n".join(out)
    if isinstance(node, list):
        if not node:
            return f"{pad}[] (빈 목록)"
        return f"{pad}[{len(node)}개] 첫 원소:\n" + skeleton(node[0], depth + 1)
    return f"{pad}{node!r}"[:120]


def series_verdict(doc) -> str:
    """'여러 날치인가'에만 답한다. 목록과 그 안의 날짜 필드를 찾는다."""
    lists: list[tuple[str, list]] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list) and node:
            lists.append((path or "(최상위)", node))
            walk(node[0], f"{path}[0]")

    walk(doc, "")
    if not lists:
        return "  판정: 목록이 없다 -- 하루치 단일 객체로 보인다."

    out = []
    for path, items in lists:
        first = items[0]
        if not isinstance(first, dict):
            out.append(f"  {path}: [{len(items)}개] (원소가 dict 아님)")
            continue
        date_keys = [k for k in first if looks_like_date(k)]
        if date_keys and len(items) > 1:
            key = date_keys[0]
            got = [str(row.get(key)) for row in items[:6] if isinstance(row, dict)]
            out.append(f"  {path}: [{len(items)}개] 날짜키 '{key}' -> {got}"
                       f"  ← **여러 날치**")
        else:
            out.append(f"  {path}: [{len(items)}개] 날짜키 {date_keys or '없음'}")
    return "\n".join(out)


def probe_index() -> None:
    print("=" * 70)
    print("Q1. 코스피 지수 투자자동향을 여러 날치로 주는 경로가 있는가")
    print("=" * 70)
    for url in INDEX_CANDIDATES:
        code, doc, note = get(url, HEADERS)
        print(f"\n--- {url}")
        print(f"    HTTP {code}{('  ' + note) if note else ''}")
        if doc is None:
            continue
        if isinstance(doc, str):
            days = re.findall(r"\d{4}\.\d{2}\.\d{2}", doc)
            uniq = sorted(set(days), reverse=True)
            print(f"    HTML {len(doc):,}자, 날짜꼴 {len(days)}개 / 고유 {len(uniq)}개")
            if len(uniq) > 1:
                print(f"    날짜: {uniq[:12]}  ← **여러 날치**")
                # 표 한 줄이 어떻게 생겼는지 첫 날짜 주변만 잘라 본다.
                i = doc.find(uniq[0])
                print("    --- 첫 날짜 주변 ---")
                print("    " + " ".join(doc[i:i + 700].split())[:600])
            continue
        if isinstance(doc, dict):
            print(f"    최상위 키: {', '.join(doc.keys())}")
        elif isinstance(doc, list):
            print(f"    최상위가 목록 [{len(doc)}개]")
        print(series_verdict(doc))
        print("    --- 구조 ---")
        print(skeleton(doc, 2)[:2000])


def probe_rank() -> None:
    print("\n" + "=" * 70)
    print("Q2. 수급 상위 API 에 시장 전체 순매수액이 딸려 오는가")
    print("=" * 70)
    qs = urllib.parse.urlencode({
        "investorType": "FOREIGNER", "tradeType": "KRX",
        "marketType": "KOSPI", "startIdx": 0, "pageSize": 20, "periodType": "DAY",
    })
    code, doc, note = get(f"{RANK_URL}?{qs}", RANK_HEADERS)
    print(f"HTTP {code}{('  ' + note) if note else ''}")
    if not isinstance(doc, dict):
        return
    print(f"최상위 키: {', '.join(doc.keys())}")
    sections = doc.get("sections") or {}
    rest = {k: v for k, v in sections.items() if k != "buyRankList"}
    print("\nbuyRankList 를 뺀 나머지:")
    print(skeleton(rest, 1) if rest else "  (없음 -- 상위 목록이 전부다)")


def main() -> int:
    probe_index()
    probe_rank()
    print("\n" + "=" * 70)
    print("읽는 법: Q1 에서 '← **여러 날치**' 가 붙은 줄이 있으면 봇이 그 경로를")
    print("직접 받아 쓰면 된다. 하나도 없으면 Monthly 시트를 읽거나 날마다")
    print("피드에 쌓아야 한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
