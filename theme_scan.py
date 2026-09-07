"""
Daily Naver theme scan + merge pipeline.

Scans page 1 of https://finance.naver.com/sise/theme.naver, finds themes with
>=3 stocks that have market cap >= MIN_CAP(억) and change rate >= MIN_RATE(%),
then merges overlapping themes (overlap coefficient >= MERGE_THRESHOLD) and
applies a manual keyword override list to combine loosely-related theme
families (e.g. all COVID/pharma variants under "바이오").

Usage:
    python scan_and_merge.py [output_dir]

Writes to <output_dir> (default: script's own directory):
    - raw_scan.json      : every qualifying theme, unmerged (audit trail)
    - final_groups.json  : final merged groups with top5 stocks each
    - final_groups.txt   : human-readable version of the above
"""
import re
import sys
import json
import time
import urllib.request
import http.cookiejar
from pathlib import Path

HEADERS = {"User-Agent": "Mozilla/5.0"}
MIN_CAP = 2000       # 억원
MIN_RATE = 5.0        # %
MIN_COUNT = 3          # qualifying stocks needed for a theme to count
MERGE_THRESHOLD = 0.6  # overlap coefficient for auto-merge

# Keyword override: any theme whose name matches (substring) one of these
# patterns gets folded into the given keyword group, taking priority over
# the automatic overlap-based merge. Edit this list (or point KEYWORD_FILE
# at C:\Users\USER\Desktop\클로드\키워드.json) to steer future runs.
KEYWORD_PATTERNS = {
    "바이오": [
        "코로나19", "백신", "진단시약", "방역", "바이오시밀러", "제약업체",
        "비만치료제", "mRNA", "치매", "면역항암제",
    ],
    "2차전지": [
        "2차전지", "리튬", "폐배터리", "전력저장장치", "리비안",
    ],
    "비철금속": ["귀금속", "비철금속"],
}

# User-editable source of truth, kept in this repo as keywords.json. It used
# to live on the user Desktop, which a GitHub runner cannot see. Each entry
# "source_themes" (exact theme names from a past run) are folded in as
# exact-match patterns on top of the substring patterns above, so edits are
# picked up on the next run. Edit it on GitHub, or locally and push.
KEYWORD_FILE = Path(__file__).with_name("keywords.json")


def load_keyword_overrides():
    patterns = {k: list(v) for k, v in KEYWORD_PATTERNS.items()}
    if not KEYWORD_FILE.exists():
        return patterns
    try:
        with open(KEYWORD_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] could not read {KEYWORD_FILE}: {e}", file=sys.stderr)
        return patterns
    for entry in data:
        kw = entry.get("keyword")
        themes = entry.get("source_themes", [])
        if not kw or not themes:
            continue
        patterns.setdefault(kw, [])
        for t in themes:
            if t not in patterns[kw]:
                patterns[kw].append(t)
    return patterns


def fetch(opener, url):
    req = urllib.request.Request(url, headers=HEADERS)
    with opener.open(req, timeout=15) as resp:
        raw = resp.read()
    return raw.decode("euc-kr", errors="ignore")


def scan_themes():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    # enable amount(거래대금) + market_sum(시가총액) columns, persists via cookie
    fetch(opener, "https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no=593")
    fetch(opener, "https://finance.naver.com/sise/field_submit.naver?menu=theme&returnUrl=http%3A%2F%2Ffinance.naver.com%2Fsise%2Fsise_group_detail.naver%3Ftype%3Dtheme%26no%3D593&fieldIds=amount&fieldIds=market_sum")

    theme_list_html = fetch(opener, "https://finance.naver.com/sise/theme.naver")
    themes = re.findall(
        r'sise_group_detail\.naver\?type=theme&no=(\d+)">([^<]+)</a>',
        theme_list_html,
    )

    name_pat = re.compile(r'<a href="/item/main\.naver\?code=([0-9A-Za-z]{6})">([^<]*)</a>')
    rate_pat = re.compile(r'class="tah p11[^"]*">\s*([+\-]?[\d,.]+)%\s*</span>')
    cap_pat = re.compile(r'<td class="number" style="padding-right:20px;">([\d,\-]+)</td>')
    table_marker = '업종별 시세 리스트'

    results = []
    for theme_id, theme_name in themes:
        url = f"https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={theme_id}"
        html = fetch(opener, url)
        start = html.find(table_marker)
        end = html.find('</table>', start) if start != -1 else -1
        table_html = html[start:end] if start != -1 and end != -1 else html

        codes_names = name_pat.findall(table_html)
        rates = rate_pat.findall(table_html)
        nums = cap_pat.findall(table_html)
        amounts = nums[0::2]
        caps = nums[1::2]

        qualifying = []
        if len(codes_names) == len(rates) == len(caps) == len(amounts):
            for (code, name), rate_str, amount_str, cap_str in zip(codes_names, rates, amounts, caps):
                cap_str = cap_str.replace(",", "")
                amount_str = amount_str.replace(",", "")
                if not cap_str.isdigit() or not amount_str.isdigit():
                    continue
                rate = float(rate_str.replace(",", ""))
                cap = int(cap_str)
                amount = int(amount_str)
                if cap >= MIN_CAP and rate >= MIN_RATE:
                    qualifying.append({"name": name, "code": code, "rate": rate, "cap": cap, "amount": amount})
        else:
            print(f"[WARN] {theme_name}: parse count mismatch "
                  f"(names={len(codes_names)}, rates={len(rates)}, caps={len(caps)}, amounts={len(amounts)})",
                  file=sys.stderr)

        if len(qualifying) >= MIN_COUNT:
            results.append({"theme_name": theme_name, "theme_id": theme_id, "qualifying": qualifying})
        time.sleep(0.12)

    return results


def merge_by_overlap(themes):
    n = len(themes)
    sets = [set(s["code"] for s in t["qualifying"]) for t in themes]
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            inter = sets[i] & sets[j]
            if not inter:
                continue
            overlap_coef = len(inter) / min(len(sets[i]), len(sets[j]))
            if overlap_coef >= MERGE_THRESHOLD:
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [[themes[i] for i in idxs] for idxs in groups.values()]


def keyword_for(theme_name, patterns):
    # Exact names win over substrings, and are checked across every keyword
    # before any substring is tried. keywords.json holds exact theme names, and
    # one is often a prefix of another ("LED" and "LED장비"): with substring
    # matching alone, whichever keyword happened to be checked first would
    # swallow the longer theme into the wrong group.
    for keyword, pats in patterns.items():
        if theme_name in pats:
            return keyword
    for keyword, pats in patterns.items():
        for p in pats:
            if p in theme_name:
                return keyword
    return None


def apply_keyword_overrides(auto_groups):
    """Re-merge auto_groups: any group containing a theme matched by a
    keyword pattern gets folded into that keyword's supergroup."""
    patterns = load_keyword_overrides()
    keyword_buckets = {}   # keyword -> list of theme dicts
    leftover_groups = []   # groups with no keyword match, kept as auto-merged

    for group in auto_groups:
        matched_keyword = None
        for t in group:
            kw = keyword_for(t["theme_name"], patterns)
            if kw:
                matched_keyword = kw
                break
        if matched_keyword:
            keyword_buckets.setdefault(matched_keyword, []).extend(group)
        else:
            leftover_groups.append(group)

    final = []
    for keyword, theme_list in keyword_buckets.items():
        final.append({"keyword": keyword, "themes": theme_list})
    for group in leftover_groups:
        short_label = min((t["theme_name"] for t in group), key=len)
        short_label = re.sub(r"\([^)]*\)", "", short_label).strip()
        final.append({"keyword": short_label, "themes": group})
    return final


def build_output(final_groups):
    out = []
    for g in final_groups:
        stock_map = {}
        source_names = []
        for t in g["themes"]:
            source_names.append(t["theme_name"])
            for s in t["qualifying"]:
                stock_map[s["code"]] = s
        top5 = sorted(stock_map.values(), key=lambda s: -s["rate"])[:5]
        out.append({
            "keyword": g["keyword"],
            "source_themes": sorted(set(source_names)),
            "total_qualifying": len(stock_map),
            "top5": top5,
        })
    out.sort(key=lambda g: -max((s["rate"] for s in g["top5"]), default=0))
    return out


def main():
    args = [a for a in sys.argv[1:] if a != "--regroup"]
    regroup = "--regroup" in sys.argv
    out_dir = Path(args[0]) if args else Path(__file__).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    if regroup:
        # Re-derive the groups from the scan already on disk. llm_group.py
        # rewrites keywords.json between the two passes, and re-fetching ~40
        # theme pages to apply it would both waste the runner's time and read
        # a market that has moved since the first pass.
        with open(out_dir / "raw_scan.json", encoding="utf-8") as f:
            raw = json.load(f)
        print(f"[regroup] reusing raw_scan.json ({len(raw)} themes)", file=sys.stderr)
    else:
        raw = scan_themes()
        with open(out_dir / "raw_scan.json", "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)

    auto_groups = merge_by_overlap(raw)
    final_groups = apply_keyword_overrides(auto_groups)
    final = build_output(final_groups)

    with open(out_dir / "final_groups.json", "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    with open(out_dir / "final_groups.txt", "w", encoding="utf-8") as f:
        f.write(f"=== 최종 {len(final)}개 그룹 ===\n")
        for g in final:
            f.write(f"\n[{g['keyword']}] (원본 테마 {len(g['source_themes'])}개: {', '.join(g['source_themes'])}) - 조건충족 {g['total_qualifying']}종목 중 상위5\n")
            for s in g["top5"]:
                f.write(f"  - {s['name']}({s['code']}): 등락률 {s['rate']:+.2f}%, 시가총액 {s['cap']:,}억, 거래대금 {s['amount']:,}백만\n")

    print(f"themes qualifying: {len(raw)}", file=sys.stderr)
    print(f"final groups: {len(final)}", file=sys.stderr)


if __name__ == "__main__":
    main()
