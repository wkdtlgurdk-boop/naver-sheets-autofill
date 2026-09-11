"""오늘의 코스피/코스닥 지수·거래대금(조)·외국인 순매수(억)를 JSON 으로 찍는다.

2026-09-11 에 네이버가 finance.naver.com 을 Next.js 화면으로 갈아치우면서 옛
HTML 파싱(id="now_value" 등)이 죽었다. 같은 값을 m.stock.naver.com 의 JSON 이
주므로 그쪽으로 옮긴다. 세 경로를 쓴다:

    /basic        지수와 등락률, 그리고 그 값이 언제 찍힌 것인지(localTradedAt)
    /integration  totalInfos 안에 '대금'이 백만원 단위로 들어 있다
    /trend        외국인·기관·개인 순매수 (억원)

단위는 옛 HTML 과 같아서 시트에 쓰는 값도 그대로다.

사용: python market_summary.py
"""
import json
import sys
import urllib.request

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://m.stock.naver.com/",
    "User-Agent": "Mozilla/5.0 naverstock-web-api-skill/1.0",
}
API = "https://m.stock.naver.com/api/index/{code}/{path}"


def fetch(code, path):
    req = urllib.request.Request(API.format(code=code, path=path), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def num(value):
    """'6,909.91' 이나 '-22,984' 를 숫자로. 단위 글자는 떼어낸다."""
    if value in (None, "", "N/A", "-"):
        return None
    text = "".join(ch for ch in str(value) if ch in "0123456789.-+")
    try:
        return float(text)
    except ValueError:
        return None


def total_info(integration, code):
    """integration 의 totalInfos 에서 code 에 해당하는 값을 꺼낸다."""
    for row in integration.get("totalInfos") or []:
        if row.get("code") == code:
            return num(row.get("value"))
    return None


def parse_index(code):
    basic = fetch(code, "basic")
    index = num(basic.get("closePrice"))
    if index is None:
        raise SystemExit(f"[중단] {code} 지수를 읽지 못했습니다. "
                         f"받은 키: {sorted(basic)}")

    amount_mn = total_info(fetch(code, "integration"), "accumulatedTradingValue")
    foreign = num((fetch(code, "trend") or {}).get("foreignValue"))
    return {
        "index": index,
        # 백만원 -> 조원. 옛 HTML 도 백만 단위였으므로 계산식이 같다.
        "amount_trillion": round(amount_mn / 1_000_000, 1) if amount_mn else None,
        "foreign_eok": int(foreign) if foreign is not None else None,
        # 이 값이 언제 찍힌 것인지. 애프터마켓이 생기면서 15:30 이후로도 움직인다.
        "traded_at": basic.get("localTradedAt"),
        "market_status": basic.get("marketStatus"),
    }


def main():
    result = {"KOSPI": parse_index("KOSPI"), "KOSDAQ": parse_index("KOSDAQ")}
    for code, v in result.items():
        print(f"{code}: {v['index']:,} / {v['amount_trillion']}조 / "
              f"외인 {v['foreign_eok']:+,}억  ({v['market_status']} {v['traded_at']})",
              file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
