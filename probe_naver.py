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
    """문턱 후보별로 금요일 종가에 무엇이 잡혔을지 센다. 고르기 위한 재료다."""
    import json as _json

    def n(v):
        if v in (None, "", "N/A", "-"):
            return None
        try:
            return float(str(v).replace(",", "").replace("+", ""))
        except ValueError:
            return None

    # (이름, 규칙) — 규칙은 (상승률, 시총) 쌍의 목록이고 하나라도 맞으면 통과
    RULES = {
        "현재":            [(5.0, 2000), (20.0, 500)],
        "+10%/1,000억 추가": [(5.0, 2000), (10.0, 1000), (20.0, 500)],
        "+15%/1,000억 추가": [(5.0, 2000), (15.0, 1000), (20.0, 500)],
        "+10%/1,500억 추가": [(5.0, 2000), (10.0, 1500), (20.0, 500)],
    }
    MIN_COUNT, MIN_PART, PART_RATE = 3, 0.2, 3.0

    groups, seen = [], set()
    for page in range(1, 4):
        _, raw = get(f"https://m.stock.naver.com/api/stocks/theme?page={page}&pageSize=100")
        gs = [g for g in _json.loads(raw.decode())["groups"] if g["no"] not in seen]
        if not gs:
            break
        for g in gs:
            seen.add(g["no"])
        groups += gs
    print(f"테마 {len(groups)}개 조회")

    detail = {}
    for g in groups:
        _, raw = get(f"https://m.stock.naver.com/api/stocks/theme/{g['no']}?page=1&pageSize=100")
        rows = []
        for st in _json.loads(raw.decode()).get("stocks") or []:
            rate, cap = n(st.get("fluctuationsRatio")), n(st.get("marketValue"))
            if None not in (rate, cap):
                rows.append((st.get("stockName"), rate, cap))
        detail[g["no"]] = rows

    for label, rule in RULES.items():
        hits = []
        for g in groups:
            rows = detail[g["no"]]
            if not rows:
                continue
            ups = sum(1 for _, r, _c in rows if r >= PART_RATE)
            if ups / len(rows) < MIN_PART:
                continue
            q = [nm for nm, r, c in rows if any(r >= mr and c >= mc for mr, mc in rule)]
            if len(q) >= MIN_COUNT:
                hits.append((g["name"], len(q)))
        names = ", ".join(f"{nm}({c})" for nm, c in hits)
        print(f"\n[{label}] 테마 {len(hits)}개")
        print(f"   {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
