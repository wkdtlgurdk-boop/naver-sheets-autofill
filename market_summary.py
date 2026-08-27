"""
Fetch today's KOSPI/KOSDAQ index, total trading value(조), and foreign net
buying(억) from Naver Finance. Prints a small JSON to stdout.

Usage: python market_summary.py
"""
import re
import json
import urllib.request

HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    return raw.decode("euc-kr", errors="ignore")


def parse_index(code):
    html = fetch(f"https://finance.naver.com/sise/sise_index.naver?code={code}")
    now_value = re.search(r'id="now_value">([\d,.]+)<', html).group(1)
    amount = re.search(r'id="amount">([\d,]+)</td>', html).group(1)
    foreign = re.search(r'외국인<br><span class="(?:up|dn)">([+\-][\d,]+)<span>억</span>', html)
    return {
        "index": float(now_value.replace(",", "")),
        "amount_trillion": round(int(amount.replace(",", "")) / 1_000_000, 1),
        "foreign_eok": int(foreign.group(1).replace(",", "").replace("+", "")) if foreign else None,
    }


def main():
    result = {
        "KOSPI": parse_index("KOSPI"),
        "KOSDAQ": parse_index("KOSDAQ"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
