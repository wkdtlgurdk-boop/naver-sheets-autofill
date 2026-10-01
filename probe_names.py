#!/usr/bin/env python3
"""일회용 진단 (2026-10-01). 종목코드 하나로 등락률·시총·거래대금을 주는 경로를 찾는다.

직접테마 종목 중 어느 네이버 테마에도 없는 것(이브이첨단소재 등)은 테마 스캔에서
시세를 못 얻는다. basic 경로는 등락률은 주지만 시총·거래대금이 없다.
"""
import json
import urllib.request

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


for code in ("131400", "005930"):
    for path in ("integration", "basic", "price?pageSize=1&page=1"):
        url = f"https://m.stock.naver.com/api/stock/{code}/{path}"
        try:
            doc = get(url)
        except Exception as exc:
            print(f"{code} {path} 실패 {exc}")
            continue
        if isinstance(doc, dict):
            print(f"{code} {path} keys={sorted(doc)}")
            for k in ("totalInfos", "dealTrendInfos"):
                if isinstance(doc.get(k), list):
                    print("   ", k, [(i.get("code"), i.get("key"), i.get("value")) for i in doc[k][:20]])
        else:
            print(f"{code} {path} list len={len(doc)} first={json.dumps(doc[0], ensure_ascii=False)[:400] if doc else None}")
# 테마 종목 목록 한 행의 키 (비교용)
d = get("https://m.stock.naver.com/api/stocks/theme/64?page=1&pageSize=1")
print("theme row keys", sorted((d.get("stocks") or [{}])[0]))
