#!/usr/bin/env python3
"""일회용 진단 (2026-09-26). 공매도·대차잔고와 종목분석 리포트를 어디서 받을 수
있는지 러너가 찔러 본다. 개발 세션에서는 네이버·KRX 에 붙을 수 없다.

키 이름·자료형·목록 길이와 첫 원소만 찍는다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

NAVER = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://stock.naver.com/",
    "User-Agent": "Mozilla/5.0 probe/1.0",
}
KRX = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}

end = date.today()
start = end - timedelta(days=10)
S, E = f"{start:%Y%m%d}", f"{end:%Y%m%d}"

GETS = [
    "https://m.stock.naver.com/api/stock/005930/shortSelling",
    "https://m.stock.naver.com/api/stock/005930/shortSellingTrend",
    "https://m.stock.naver.com/api/stock/005930/short",
    "https://m.stock.naver.com/api/stock/005930/integration",
    "https://api.stock.naver.com/stock/005930/integration",
    "https://m.stock.naver.com/api/stock/005930/lendingTrend",
    f"https://stock.naver.com/api/stockSecurity/researches/v2/company?index=0&size=3&startDate={start}&endDate={end}",
    f"https://stock.naver.com/api/stockSecurity/researches/v2/company?index=0&size=3&startDate={end - timedelta(days=3)}&endDate={end}",
    "https://m.stock.naver.com/api/research/company?pageSize=3&page=1",
    "https://m.stock.naver.com/api/stock/005930/research?pageSize=3",
    "https://m.stock.naver.com/api/stock/005930/consensus",
]

KRX_POSTS = [
    ("공매도 종합(개별)", {"bld": "dbms/MDC/STAT/srt/MDCSTAT30102", "isuCd": "KR7005930003",
                        "strtDd": S, "endDd": E}),
    ("공매도 거래 전종목", {"bld": "dbms/MDC/STAT/srt/MDCSTAT30101", "mktId": "STK",
                         "trdDd": E, "inqCond": "STMFRTSCIFDRFS"}),
    ("공매도 잔고 전종목", {"bld": "dbms/MDC/STAT/srt/MDCSTAT30501", "mktId": "STK",
                         "trdDd": E}),
    ("대차거래 전종목", {"bld": "dbms/MDC/STAT/srt/MDCSTAT30301", "mktId": "STK",
                       "strtDd": S, "endDd": E}),
]


def show(label: str, code, body) -> None:
    print(f"\n== {label}\n   HTTP {code}")
    if isinstance(body, dict):
        print(f"   dict keys: {sorted(body)[:40]}")
        for k, v in body.items():
            if isinstance(v, list) and v:
                print(f"   {k}: list[{len(v)}], first keys {sorted(v[0])[:30] if isinstance(v[0], dict) else type(v[0]).__name__}")
                print(f"   first: {json.dumps(v[0], ensure_ascii=False)[:600]}")
            elif isinstance(v, dict):
                print(f"   {k}: dict keys {sorted(v)[:30]}")
    elif isinstance(body, list):
        print(f"   list[{len(body)}]")
        if body:
            print(f"   first: {json.dumps(body[0], ensure_ascii=False)[:600]}")
    else:
        print(f"   text: {str(body)[:300]!r}")


def fetch(req) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return -1, repr(e)


DAUM = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://finance.daum.net/quotes/A005930",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
}
# 2라운드: 1라운드에서 네이버에는 공매도 경로가 없고 KRX 는 로그인을 요구했다
# ('LOGOUT'). 다음 후보들.
ROUND2 = [
    ("daum", "https://finance.daum.net/api/quote/A005930/short_sellings?page=1&perPage=5"),
    ("daum", "https://finance.daum.net/api/quote/A005930/short_selling?page=1&perPage=5"),
    ("daum", "https://finance.daum.net/api/investor/short_selling?page=1&perPage=5"),
    ("daum", "https://finance.daum.net/api/quote/A005930/lending?page=1&perPage=5"),
    ("daum", "https://finance.daum.net/api/quotes/A005930?summary=false&changeStatistics=true"),
    ("naver", "https://finance.naver.com/item/short_trade.naver?code=005930"),
    ("naver", "https://m.stock.naver.com/api/stock/005930/shortSellingInfo"),
    ("naver", "https://m.stock.naver.com/api/stock/005930/lendingBalance"),
    ("krx", "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20260923"),
]
for kind, url in ROUND2:
    hdr = {"daum": DAUM, "naver": NAVER, "krx": {"User-Agent": "Mozilla/5.0"}}[kind]
    show(url, *fetch(urllib.request.Request(url, headers=hdr)))

import sys
if "--round1" not in sys.argv:
    raise SystemExit(0)

for url in GETS:
    show(url, *fetch(urllib.request.Request(url, headers=NAVER)))

for label, form in KRX_POSTS:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request("http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
                                 data=data, headers=KRX, method="POST")
    show(f"KRX {label}", *fetch(req))
