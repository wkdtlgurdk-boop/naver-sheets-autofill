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
    """MLCC 테마가 왜 조건충족에서 빠졌는지 종목 단위로 본다."""
    import json as _json
    MIN_CAP, MIN_RATE, SURGE_CAP, SURGE_RATE = 2000, 5.0, 500, 20.0

    def n(v):
        if v in (None, "", "N/A", "-"):
            return None
        try:
            return float(str(v).replace(",", "").replace("+", ""))
        except ValueError:
            return None

    def why(rate, cap):
        if rate >= SURGE_RATE and cap >= SURGE_CAP:
            return "통과(급등)"
        if rate >= MIN_RATE and cap >= MIN_CAP:
            return "통과(일반)"
        if rate < MIN_RATE:
            return f"탈락: 상승률 {rate:.2f}% < {MIN_RATE:g}%"
        return f"탈락: 시총 {cap:,.0f}억 < {MIN_CAP:,}억"

    # 405 = MLCC, 566 = 딥페이크, 55 = 보안주(정보), 426 = 양자암호
    for no, label in ((405, "MLCC(적층세라믹콘덴서)"), (566, "딥페이크"),
                      (55, "보안주(정보)")):
        _, raw = get(f"https://m.stock.naver.com/api/stocks/theme/{no}?page=1&pageSize=100")
        stocks = _json.loads(raw.decode()).get("stocks") or []
        print(f"\n=== {label} — 구성 {len(stocks)}종목")
        rows = []
        for st in stocks:
            rate, cap = n(st.get("fluctuationsRatio")), n(st.get("marketValue"))
            if None in (rate, cap):
                continue
            rows.append((rate, cap, st.get("stockName"), why(rate, cap)))
        rows.sort(key=lambda r: -r[0])
        ok = 0
        for rate, cap, name, verdict in rows:
            mark = "O" if verdict.startswith("통과") else " "
            ok += verdict.startswith("통과")
            print(f"  {mark} {name:14} {rate:+7.2f}%  시총 {cap:>9,.0f}억   {verdict}")
        print(f"  -> 조건충족 {ok}종목 (3종목 이상이어야 테마로 잡힘)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
