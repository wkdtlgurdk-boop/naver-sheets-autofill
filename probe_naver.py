#!/usr/bin/env python3
"""일회용 진단. 네트워크가 막힌 개발 세션 대신 러너가 네이버를 찔러 본다.

## 밝혀진 것 (2026-09-23 실측)

아침 알림의 수급 머리글을 '코스피 시장 전체 순매수'로 바꾸면서 물었던 것과
그 답이다. 경로가 또 죽는 날 여기서부터 보라고 남긴다.

    /api/index/KOSPI/trend                200  하루치 단일 객체
        bizdate / personalValue / foreignValue / institutionalValue
        **bizdate 파라미터가 먹는다** -- ?bizdate=20260922 로 그 날을 받는다.
        거래일마다 한 번씩 불러 열흘을 만드는 근거가 이것이다.
    /api/index/KOSPI/price?pageSize=10    200  지수 OHLC 열흘치.
        localTradedAt 이 실제로 장이 선 날짜라 휴장일 달력으로 쓴다.
    /api/index/KOSPI/investorTrend        404
    api.stock.naver.com/index/KOSPI/investorTrend        404
    api.stock.naver.com/chart/domestic/index/KOSPI/investor  400
    finance.naver.com/sise/investorDealTrendDay.naver    410 (네이버가 지웠다)

즉 **여러 날치를 한 번에 주는 경로는 없다.** 하루치를 날마다 부르는 수밖에
없고, 그게 tradesignal/naver.py 의 fetch_index_flows 다.

## 값은 안 찍는다

키 이름과 자료형, 목록 길이만 본다. 숫자는 '여러 날치인가'를 가르는 데
필요한 만큼만(날짜 필드로 보이는 것) 찍는다.
"""
from __future__ import annotations

import json
import re
import urllib.error
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
    print("코스피 지수 투자자동향 경로가 아직 살아 있는가")
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


def main() -> int:
    probe_index()
    print("\n" + "=" * 70)
    print("trend 가 200 이고 bizdate 가 먹으면 fetch_index_flows 는 멀쩡하다.")
    print("price 가 죽으면 휴장일 달력이 없어지므로 거기부터 고쳐야 한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
