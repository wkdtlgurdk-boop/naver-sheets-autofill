#!/usr/bin/env python3
"""일회용 진단 (2026-10-01). 직접테마 종목 중 시세를 못 찾은 이름의 네이버 표기·코드를 찾는다.

custom_themes.json 은 네이버 테마에서 본 종목의 시세만 쓴다. 어느 네이버 테마에도
없는 종목이거나 이름이 다르면 '[직접테마] 시세를 못 찾은 종목' 에 찍힌다.
검색 자동완성으로 표기와 코드를 찾고, 코드로 종목 기본 시세가 오는지 본다.
"""
import json
import urllib.parse
import urllib.request

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
NAMES = ["이브이첨단소재", "중앙첨단소재", "대진첨단소재", "씨아이에스", "유일에너테크",
         "코윈테크", "제일엠앤에스", "디이엔티",
         "덕산데코피아", "일진디스플레이", "케이피에스"]


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


for name in NAMES:
    q = urllib.parse.quote(name)
    for url in (f"https://ac.stock.naver.com/ac?q={q}&target=stock",
                f"https://m.stock.naver.com/front-api/search/autoComplete?query={q}&target=stock"):
        try:
            doc = get(url)
        except Exception as exc:
            print(f"{name} | {url.split('?')[0]} 실패 {exc}")
            continue
        items = doc.get("items") or (doc.get("result") or {}).get("items") or []
        hits = [(i.get("name"), i.get("code"), i.get("typeCode") or i.get("category"))
                for i in items[:4]] if isinstance(items, list) else items
        print(f"{name} | {url.split('?')[0]} -> {hits}")
        if hits and isinstance(hits, list) and hits[0][1]:
            code = hits[0][1]
            try:
                b = get(f"https://m.stock.naver.com/api/stock/{code}/basic")
                print(f"   basic {code}: stockName={b.get('stockName')} "
                      f"keys={sorted(b)[:30]}")
            except Exception as exc:
                print(f"   basic {code} 실패 {exc}")
        break
