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
    print("=" * 70)
    print("1) 후보 엔드포인트")
    print("=" * 70)
    for url in CANDIDATES:
        show(url)

    return 0


def _unused():
    print("=" * 70)
    print("2) 새 테마 페이지 안에서 단서 찾기")
    print("=" * 70)
    page = None
    for url in ("https://finance.naver.com/sise/theme.naver",
                "https://stock.naver.com/sise/theme"):
        try:
            status, raw = get(url, timeout=25)
        except Exception as e:  # noqa: BLE001
            print(f"  {url} — {type(e).__name__}: {e}")
            continue
        body = raw.decode("euc-kr" if "finance." in url else "utf-8",
                          errors="replace")
        print(f"  {url} -> {status}, {len(body):,}자")
        page = page or body

        # 페이지가 참조하는 API 주소를 전부 긁는다. 새 프론트엔드가 무엇을
        # 부르는지는 대개 번들이 아니라 초기 데이터에 그대로 적혀 있다.
        apis = sorted(set(re.findall(r'https?://[\w.-]*naver\.com/[\w/.\-?=&%]*api[\w/.\-?=&%]*', body)))
        apis += sorted(set(re.findall(r'"(/api/[\w/.\-?=&%]+)"', body)))
        for a in apis[:25]:
            print(f"     api: {a}")
        if not apis:
            print("     (api 로 보이는 주소 없음)")

        # 테마 식별자가 어떤 모양으로 들어 있는지
        for pat, label in (
            (r'themeCode["\':\s]+([A-Za-z0-9]+)', "themeCode"),
            (r'theme[Ii]d["\':\s]+([A-Za-z0-9]+)', "themeId"),
            (r'no=(\d+)[^>]*>([^<]{2,20})</a>', "옛 링크"),
            (r'"themeName"\s*:\s*"([^"]+)"', "themeName"),
        ):
            found = re.findall(pat, body)[:5]
            if found:
                print(f"     {label}: {found}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
