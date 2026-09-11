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
    """페이지를 줄인 게 테마 수를 깎았는지 직접 센다.

    상위 100개(현재 설정) 밖에, 조건을 통과할 만한 테마가 남아 있는지 본다.
    theme_scan 과 같은 문턱을 그대로 쓴다.
    """
    import json as _json
    MIN_CAP, MIN_RATE, SURGE_CAP, SURGE_RATE, MIN_COUNT = 2000, 5.0, 500, 20.0, 3
    MIN_PART = 0.2

    def n(v):
        if v in (None, "", "N/A", "-"):
            return None
        try:
            return float(str(v).replace(",", "").replace("+", ""))
        except ValueError:
            return None

    def qualifies(rate, cap):
        if rate >= SURGE_RATE and cap >= SURGE_CAP:
            return True
        return rate >= MIN_RATE and cap >= MIN_CAP

    all_groups, seen = [], set()
    for page in range(1, 4):
        url = f"https://m.stock.naver.com/api/stocks/theme?page={page}&pageSize=100"
        _, raw = get(url)
        gs = [g for g in _json.loads(raw.decode())["groups"] if g["no"] not in seen]
        if not gs:
            break
        for g in gs:
            seen.add(g["no"])
        all_groups += gs
        rates = [n(g["changeRate"]) for g in gs]
        print(f"{page}페이지: {len(gs)}개, 등락률 {max(rates):+.2f}% ~ {min(rates):+.2f}%")

    print(f"\n전체 {len(all_groups)}개")
    passed = [g for g in all_groups
              if g["totalCount"] and g["riseCount"] / g["totalCount"] >= MIN_PART]
    in100 = [g for g in passed if all_groups.index(g) < 100]
    print(f"참여율 {MIN_PART:.0%} 통과: {len(passed)}개 (상위100 안 {len(in100)}, 밖 {len(passed)-len(in100)})")

    print("\n=== 참여율 통과 테마의 상세를 받아 조건충족 종목을 센다")
    hits = []
    for i, g in enumerate(passed):
        _, raw = get(f"https://m.stock.naver.com/api/stocks/theme/{g['no']}?page=1&pageSize=100")
        stocks = _json.loads(raw.decode()).get("stocks") or []
        q = [s for s in stocks
             if None not in (n(s.get("fluctuationsRatio")), n(s.get("marketValue")))
             and qualifies(n(s["fluctuationsRatio"]), n(s["marketValue"]))]
        if len(q) >= MIN_COUNT:
            rank = all_groups.index(g) + 1
            hits.append((rank, g["name"], g["changeRate"], len(q)))
    print(f"\n조건충족({MIN_COUNT}종목 이상) 테마 {len(hits)}개:")
    for rank, name, rate, cnt in hits:
        mark = "  " if rank <= 100 else "★ 100위 밖!"
        print(f"  {rank:>3}위 {name} ({rate}%) — {cnt}종목 {mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
