"""네이버가 테마 목록을 어디로 옮겼는지 러너가 대신 찾아본다.

2026-09-11, finance.naver.com 의 테마 페이지가 Next.js 기반 새 화면으로 바뀌면서
옛 HTML 에서 테마 링크를 뽑던 방식이 통하지 않게 됐다. 이 세션에서는 네이버에
직접 붙을 수 없으므로(프록시 차단) 러너가 찔러보고 결과만 로그로 남긴다.

일회용 진단 도구다. 새 경로를 찾아 theme_scan.py 를 옮기고 나면 지워도 된다.
"""
import json
import re
import sys
import urllib.error
import urllib.request

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://stock.naver.com/",
    "User-Agent": "Mozilla/5.0 naverstock-web-api-skill/1.0",
}

CANDIDATES = [
    # 1차에서 살아 있는 것으로 확인된 테마 목록. 페이지 파라미터를 찾는다.
    "https://m.stock.naver.com/api/stocks/theme",
    "https://m.stock.naver.com/api/stocks/theme?page=2",
    "https://m.stock.naver.com/api/stocks/theme?page=1&pageSize=100",
    "https://m.stock.naver.com/api/stocks/theme?size=100",
    # 테마 하나(405 = MLCC)의 구성 종목을 어디서 주는지
    "https://m.stock.naver.com/api/stocks/theme/405",
    "https://m.stock.naver.com/api/stocks/theme/405/stocks",
    "https://m.stock.naver.com/api/stocks/theme/405/item",
    "https://m.stock.naver.com/api/stocks/theme/405/price",
    "https://m.stock.naver.com/api/stocks/theme/405?page=1&pageSize=100",
    # 지수: 거래대금과 외인 순매수가 어디에 있는지
    "https://m.stock.naver.com/api/index/KOSPI/basic",
    "https://m.stock.naver.com/api/index/KOSPI/price?pageSize=1&page=1",
    "https://m.stock.naver.com/api/index/KOSPI/investorTrend",
    "https://m.stock.naver.com/api/index/KOSPI/trend",
]



def get(url, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def show(url):
    try:
        status, raw = get(url)
    except urllib.error.HTTPError as e:
        print(f"  {status_mark(e.code)} {e.code}  {url}")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"  ✗  ERR  {url}  ({type(e).__name__}: {e})")
        return None
    body = raw.decode("utf-8", errors="replace")
    head = re.sub(r"\s+", " ", body[:900])
    print(f"  ✓  {status} {len(raw):>8,}B  {url}")
    print(f"        {head}")
    return body


def status_mark(code):
    return "✓" if code < 400 else "✗"


def main():
    """지수 응답의 전체 키를 본다 — 거래대금(조)이 어디 있는지."""
    import json as _json
    for code in ("KOSPI", "KOSDAQ"):
        for path in ("basic", "trend"):
            url = f"https://m.stock.naver.com/api/index/{code}/{path}"
            try:
                _, raw = get(url)
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {url} — {e}")
                continue
            data = _json.loads(raw.decode("utf-8"))
            print(f"\n=== {url}")
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, (str, int, float)) or v is None:
                        print(f"   {k} = {v!r}")
                    else:
                        print(f"   {k} : {type(v).__name__}")
            else:
                print(f"   {str(data)[:400]}")
    # 거래대금이 별도 경로에 있을 수도 있다.
    for url in (
        "https://m.stock.naver.com/api/index/KOSPI/summary",
        "https://m.stock.naver.com/api/index/KOSPI/integration",
        "https://m.stock.naver.com/api/index/KOSPI/marketValue",
        "https://api.stock.naver.com/index/KOSPI/integration",
    ):
        show(url)
    return 0
