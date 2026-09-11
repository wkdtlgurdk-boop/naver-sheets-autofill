"""
Daily Naver theme scan + merge pipeline.

Scans every page of https://finance.naver.com/sise/theme.naver, finds themes
with >=3 stocks that clear qualifies() -- cap >= MIN_CAP(억) with rate >=
MIN_RATE(%), or the relaxed cap >= SURGE_CAP for anything up SURGE_RATE% --
drops what filter_themes() rejects (exclusions.json plus the participation
floor), then merges overlapping themes (overlap coefficient >=
MERGE_THRESHOLD) and applies a manual keyword override list to combine
loosely-related theme families (e.g. all COVID/pharma variants under "바이오").

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
SURGE_CAP = 500      # 억원 — 급등주에만 적용하는 완화 문턱
SURGE_RATE = 20.0     # %
MIN_COUNT = 3          # qualifying stocks needed for a theme to count
MERGE_THRESHOLD = 0.6  # overlap coefficient for auto-merge
SCAN_PAGES = 2         # 목록에서 읽을 페이지 수 (전체는 7페이지 / 266개)
PARTICIPATION_RATE = 3.0   # % — 참여율을 셀 때 "올랐다"고 보는 문턱
MIN_PARTICIPATION = 0.2    # 구성종목 중 그만큼 오른 비율의 하한

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

# 스캔에서 아예 빼는 것들. 네이버 테마 목록은 한 번 만들어지면 지워지지
# 않아서 '일본 수출규제'(2019) 같은 옛 이벤트 테마가 그대로 남아 있고, 그
# 안의 종목이 오늘 다른 이유로 오르면 옛 이름이 슬롯을 차지한다. 종목 구성도
# 같이 낡는다 — 통신 사업을 접은 회사가 통신 테마에 남아 있고, 수소 회사가
# 조선기자재에 들어 있다. 둘 다 이름과 사업 내용을 알아야 하는 판단이라
# 규칙으로 못 쓰고, 사람이 읽고 고치는 목록으로 둔다. llm_group.py 가 매일
# 후보를 여기에 덧붙이되, 사람이 적은 항목은 건드리지 않는다.
EXCLUSION_FILE = Path(__file__).with_name("exclusions.json")


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


def load_exclusions(path=None):
    """exclusions.json 을 {"themes": [...], "stocks": [...]} 로 읽는다.

    파일이 없거나 깨져 있으면 빈 목록이다 — 제외는 있으면 좋은 것이지
    없으면 멈춰야 하는 것이 아니다.
    """
    path = path or EXCLUSION_FILE
    empty = {"themes": [], "keywords": [], "stocks": []}
    if not path.exists():
        return empty
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] could not read {path.name}: {e}", file=sys.stderr)
        return empty
    return {"themes": list(data.get("themes") or []),
            "keywords": list(data.get("keywords") or []),
            "stocks": list(data.get("stocks") or [])}


def theme_exclusion(theme_name, rules):
    """이 테마를 빼라는 규칙. 없으면 None.

    패턴은 부분 문자열이다. 네이버의 정식 테마명은 괄호가 붙어 길고
    ('일본 수출규제(국산화)'), 사람이 목록을 적을 때 그 표기를 정확히
    옮겨 적기를 기대하면 조용히 안 걸린다.
    """
    for rule in rules:
        pat = str(rule.get("pattern") or rule.get("name") or "").strip()
        if pat and pat in theme_name:
            return rule
    return None


def matches_keyword(keyword, pats):
    """키워드가 패턴 목록에 걸리나. 정확 일치 우선, 없으면 부분 문자열.

    키워드는 매일 모델이 새로 짓는 이름이라 표기가 흔들린다('반도체 장비'와
    '반도체 장비·소재'). 정확히 적으라고 요구하면 사람이 적어둔 제외가
    어느 날 조용히 안 걸린다.
    """
    if not keyword:
        return False
    pats = [str(p) for p in pats]
    if keyword in pats or "*" in pats:
        return True
    return any(p and p in keyword for p in pats)


def keyword_exclusion(keyword, rules):
    """이 키워드를 통째로 빼라는 규칙. 없으면 None.

    시트에 보이는 건 테마명이 아니라 키워드라, 사람이 "이건 빼" 라고
    말하게 되는 단위도 키워드다. 키워드 -> 테마 지도는 keywords.json 이
    들고 있으므로 여기서 그걸 거꾸로 타고 내려간다.
    """
    if not keyword:
        return None
    for rule in rules:
        pat = str(rule.get("keyword") or rule.get("pattern") or "").strip()
        if pat and matches_keyword(keyword, [pat]):
            return rule
    return None


def stock_exclusion(stock, theme_name, keyword, rules):
    """이 종목을 여기서 빼라는 규칙. 없으면 None.

    themes(테마명 부분일치) 나 keywords(키워드) 로 범위를 좁힐 수 있고,
    둘 다 적으면 둘 중 하나만 맞아도 걸린다. 범한퓨얼셀은 조선기자재에
    있을 이유가 없지만 연료전지 테마에서는 대표종목이 맞다 — 전역으로
    빼면 정작 맞는 자리에서도 사라진다. 아무 범위도 안 적으면 전역이다.
    code 가 적혀 있으면 코드로, 없으면 종목명으로 맞춘다.
    """
    for rule in rules:
        code = str(rule.get("code") or "").strip()
        name = str(rule.get("name") or "").strip()
        if code:
            if code != stock.get("code"):
                continue
        elif not name or name != stock.get("name"):
            continue
        themes = rule.get("themes") or []
        keywords = rule.get("keywords") or []
        if not themes and not keywords:
            return rule
        if any(str(p) == "*" or str(p) in theme_name for p in themes):
            return rule
        if matches_keyword(keyword, keywords):
            return rule
    return None


def participation(theme):
    """구성종목 중 PARTICIPATION_RATE 이상 오른 비율. 셀 수 없으면 None.

    member_count 는 2026-09-10 에 추가됐다. 그 전에 만들어진
    raw_scan.json 을 --regroup 으로 다시 돌리면 없으므로, 모르면
    거르지 않는다 (없는 값으로 테마를 죽이지 않는다).
    """
    members = theme.get("member_count")
    if not members:
        return None
    return theme.get("up_count", 0) / members


def why_excluded(rule):
    return str(rule.get("reason") or "이유 없음")


def label_rule(rule):
    """규칙을 로그 한 칸에 들어갈 이름으로."""
    for key in ("name", "pattern", "keyword"):
        if rule.get(key):
            return str(rule[key])
    return "(이름 없는 규칙)"


def filter_themes(raw, exclusions=None, patterns=None):
    """오늘의 테마로 볼 수 없는 것들을 떨어뜨린다.

    세 가지가 순서대로 걸린다.

    1) 제외 목록의 테마 — 이름만으로 아는 옛 이벤트 테마. 테마명으로도,
       그 테마가 속한 키워드로도 지목할 수 있다. 시트에 보이는 건 키워드뿐이라
       사람이 "이건 빼" 라고 말하게 되는 단위가 키워드이기 때문이다.
    2) 제외 목록의 종목 — 그 테마의 성격과 안 맞는 종목. 빼고 나면 남은
       수가 MIN_COUNT 에 못 미칠 수 있고, 그러면 테마째 빠진다. 그게 맞다:
       엉뚱한 종목 둘을 빼고 나면 애초에 테마가 움직인 게 아니었다는 뜻이다.
    3) 참여율 — 구성종목 40개 중 3개만 오른 테마는 테마가 움직인 게 아니라
       그 3개가 움직인 것이다. 낡고 비대한 테마가 정확히 이 모양이라, 이름을
       몰라도 여기서 걸린다. 반대로 작고 뜨거운 테마는 비율이 높아 남는다.

    제외는 전부 stderr 로 남긴다. 조용히 사라지는 게 제일 나쁘다. 오늘
    아무것도 걸지 못한 규칙도 한 줄로 남긴다 — 오타로 죽은 규칙과 오늘
    걸릴 게 없었던 규칙은 파일만 봐서는 구별되지 않는다.
    """
    ex = exclusions if exclusions is not None else load_exclusions()
    patterns = patterns if patterns is not None else load_keyword_overrides()
    theme_rules = ex.get("themes", [])
    keyword_rules = ex.get("keywords", [])
    stock_rules = ex.get("stocks", [])
    used = set()

    kept = []
    for t in raw:
        name = t["theme_name"]
        keyword = keyword_for(name, patterns)

        rule = theme_exclusion(name, theme_rules)
        where = f"테마 '{name}'"
        if not rule:
            rule = keyword_exclusion(keyword, keyword_rules)
            if rule:
                where = f"테마 '{name}' (키워드 '{keyword}')"
        if rule:
            used.add(id(rule))
            print(f"[제외] {where} — {why_excluded(rule)}", file=sys.stderr)
            continue

        qualifying = []
        for s in t["qualifying"]:
            srule = stock_exclusion(s, name, keyword, stock_rules)
            if srule:
                used.add(id(srule))
                print(f"[제외] {s['name']} @ '{name}' — {why_excluded(srule)}",
                      file=sys.stderr)
                continue
            qualifying.append(s)
        if len(qualifying) < MIN_COUNT:
            print(f"[제외] 테마 '{name}' — 종목을 빼고 나니 {len(qualifying)}종목 "
                  f"(MIN_COUNT {MIN_COUNT})", file=sys.stderr)
            continue

        share = participation(t)
        if share is not None and share < MIN_PARTICIPATION:
            print(f"[제외] 테마 '{name}' — 구성 {t['member_count']}종목 중 "
                  f"{t.get('up_count', 0)}개만 {PARTICIPATION_RATE:.0f}% 이상 상승 "
                  f"({share:.0%} < {MIN_PARTICIPATION:.0%})", file=sys.stderr)
            continue

        kept.append(dict(t, qualifying=qualifying))

    idle = [label_rule(r) for r in theme_rules + keyword_rules + stock_rules
            if id(r) not in used]
    if idle:
        print(f"[제외] 오늘 아무것도 걸지 않은 규칙 {len(idle)}개: {', '.join(idle)}",
              file=sys.stderr)
    return kept


def fetch(opener, url):
    req = urllib.request.Request(url, headers=HEADERS)
    with opener.open(req, timeout=15) as resp:
        raw = resp.read()
    return raw.decode("euc-kr", errors="ignore")


def qualifies(rate, cap):
    """오늘 이 종목이 자기 테마를 대표할 만한가.

    문턱이 둘이다. 평소에는 시총 2000억 이상이 5% 넘게 오른 경우지만,
    20% 넘게 오른 종목은 시총 500억까지 받는다.

    단일 시총 문턱은 두 번 작동해서, 대표종목을 고를 때만이 아니라 테마가
    살아 있는지 셀 때도 걸린다. 9/8 광통신 테마는 5% 넘게 오른 종목이 실제로
    둘이었는데(빛과전자 +30.00%/4,102억, 한국첨단소재 +5.54%/577억) 시총이
    하나를 지워 MIN_COUNT 1/3 으로 탈락했다 — 상한가 종목을 낀 테마가
    통째로 사라진 것이다. 급등 구간에서 도는 중소형주가 그날 시장이 실제로
    반응한 곳이므로, 그 구간에서만 시총을 낮춰 센다.
    """
    if rate >= SURGE_RATE and cap >= SURGE_CAP:
        return True
    return rate >= MIN_RATE and cap >= MIN_CAP


def fetch_theme_list(opener):
    """테마 목록 앞 SCAN_PAGES 페이지를 (theme_id, name) 순서대로 돌려준다.

    네이버의 테마 목록은 전일대비 등락률 내림차순이다. 그래서 앞쪽이 오늘
    실제로 움직인 테마고, 뒤로 갈수록 '오늘 이 이름으로 부를 이유가 없는'
    테마가 나온다 — 266개를 끝까지 읽으면 그중 상당수가 옛 이벤트 테마이거나
    구성종목 몇 개만 각자 오른 것들이다. 2페이지(80개)에서 끊으면 상세
    페이지 요청이 ~270번에서 ~82번으로 줄고, 걸러야 할 것이 애초에 덜 들어온다.

    이건 목록이 등락률순이라는 데 기대는 선택이다. 네이버가 정렬을 바꾸면
    조용히 엉뚱한 80개를 보게 되므로, 시트가 이상하면 SCAN_PAGES 를 올려서
    (7이면 전체) 비교해 볼 것. 2026-09-08 실측으로는 제약업체·LED·5G 가 전부
    2페이지 밖이었는데, 그날 그 테마들은 개별 종목만 올랐고 테마 자체는
    움직이지 않았다 — 지금 기준으로는 참여율에서도 걸릴 것들이다.

    페이지 링크는 없고(실측: 정규식으로 뽑히는 page 링크 0개) 범위를 넘기면
    같은 페이지를 다시 주므로, 신규 테마가 하나도 없으면 끝으로 본다.
    """
    pat = re.compile(
        r'sise_group_detail\.naver\?type=theme&no=(\d+)">([^<]+)</a>')
    seen, order = set(), []
    for page in range(1, SCAN_PAGES + 1):
        html = fetch(opener, f"https://finance.naver.com/sise/theme.naver?&page={page}")
        new = [(tid, name) for tid, name in pat.findall(html) if tid not in seen]
        if not new:
            # 1페이지부터 비면 '끝'이 아니라 못 읽은 것이다. 여기서 응답을 그대로
            # 남긴다 — 네이버에 직접 붙어볼 수 없는 환경에서 이 몇 줄이 차단인지
            # HTML 변경인지 빈 응답인지를 가른다.
            if page == 1:
                print(f"[진단] 1페이지에서 테마 링크를 찾지 못했습니다. "
                      f"응답 {len(html):,}자.", file=sys.stderr)
                print(f"[진단] 앞부분: {re.sub(r'[ \t]+', ' ', html[:600])!r}",
                      file=sys.stderr)
                for probe in ("sise_group_detail", "theme", "로그인", "점검",
                              "차단", "captcha", "Access Denied"):
                    if probe in html:
                        print(f"[진단] '{probe}' 문자열은 응답에 있습니다.",
                              file=sys.stderr)
            break
        for tid, name in new:
            seen.add(tid)
            order.append((tid, name))
        time.sleep(0.12)
    return order


def scan_themes():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    # enable amount(거래대금) + market_sum(시가총액) columns, persists via cookie
    fetch(opener, "https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no=593")
    fetch(opener, "https://finance.naver.com/sise/field_submit.naver?menu=theme&returnUrl=http%3A%2F%2Ffinance.naver.com%2Fsise%2Fsise_group_detail.naver%3Ftype%3Dtheme%26no%3D593&fieldIds=amount&fieldIds=market_sum")

    themes = fetch_theme_list(opener)
    print(f"themes listed: {len(themes)}", file=sys.stderr)
    # 목록이 0개인 건 '오늘 오른 테마가 없다'가 아니라 목록 페이지를 못 읽었다는
    # 뜻이다(2026-09-10 23:54 실측: 네이버가 두 페이지 모두 엉뚱한 HTML 을 줬다).
    # 조용히 0개로 진행하면 그날 카드가 빈 채로 나가고 시트만 멀쩡해 보인다.
    if not themes:
        raise SystemExit(
            "[중단] 테마 목록이 0개입니다. 네이버가 예상과 다른 응답을 줬습니다 "
            "(점검·차단·페이지 구조 변경). 스캔을 신뢰할 수 없어 멈춥니다.")

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
        member_rates = []
        if len(codes_names) == len(rates) == len(caps) == len(amounts):
            for (code, name), rate_str, amount_str, cap_str in zip(codes_names, rates, amounts, caps):
                cap_str = cap_str.replace(",", "")
                amount_str = amount_str.replace(",", "")
                rate = float(rate_str.replace(",", ""))
                # 참여율은 조건 통과 여부와 무관하게 구성종목 전체로 센다.
                # 시총이 작아 대표주가 못 되는 종목도 테마가 움직였는지를
                # 말해주는 표는 던진다.
                member_rates.append(rate)
                if not cap_str.isdigit() or not amount_str.isdigit():
                    continue
                cap = int(cap_str)
                amount = int(amount_str)
                if qualifies(rate, cap):
                    qualifying.append({"name": name, "code": code, "rate": rate, "cap": cap, "amount": amount})
        else:
            print(f"[WARN] {theme_name}: parse count mismatch "
                  f"(names={len(codes_names)}, rates={len(rates)}, caps={len(caps)}, amounts={len(amounts)})",
                  file=sys.stderr)

        if len(qualifying) >= MIN_COUNT:
            results.append({
                "theme_name": theme_name,
                "theme_id": theme_id,
                "member_count": len(member_rates),
                "up_count": sum(1 for r in member_rates if r >= PARTICIPATION_RATE),
                "avg_rate": round(sum(member_rates) / len(member_rates), 2) if member_rates else None,
                "qualifying": qualifying,
            })
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
        # Label the group after the theme that carries the most of it, not the
        # one with the shortest name. Eight merged semiconductor themes -- HBM,
        # 시스템반도체, 온디바이스 AI, SK하이닉스 -- once reached the sheet under
        # the name "LED" purely because "LED" was the shortest string in the
        # group, which reads as a different sector entirely. Ties go to the
        # longer name, which is the more specific one.
        lead = max(group, key=lambda t: (len(t["qualifying"]), len(t["theme_name"])))
        label = re.sub(r"\([^)]*\)", "", lead["theme_name"]).strip()
        final.append({"keyword": label, "themes": group})
    return final


def build_output(final_groups):
    """Pick each group's five stocks, giving no stock to two groups.

    Themes overlap at the stock level even after merging: 서진시스템 belongs to
    반도체 장비, 스마트폰 and SOFC all at once, and once appeared in three of the
    day's eight slots. A stock repeated across slots spends the sheet's width
    on one name and overstates how broad the day was, so the first group to
    claim it keeps it and later groups fall through to their next-best stock.

    Groups are served in the order they appear on the sheet -- best mover
    first -- so the claim goes to the group the stock actually led.
    """
    pools = []
    for g in final_groups:
        stock_map = {}
        source_names = []
        for t in g["themes"]:
            source_names.append(t["theme_name"])
            for s in t["qualifying"]:
                stock_map[s["code"]] = s
        pools.append({
            "keyword": g["keyword"],
            "source_themes": sorted(set(source_names)),
            "total_qualifying": len(stock_map),
            "ranked": sorted(stock_map.values(), key=lambda s: -s["rate"]),
        })

    pools.sort(key=lambda p: -max((s["rate"] for s in p["ranked"]), default=0))

    taken, out = set(), []
    for p in pools:
        top5 = []
        for s in p["ranked"]:
            if s["code"] in taken:
                continue
            taken.add(s["code"])
            top5.append(s)
            if len(top5) == 5:
                break
        if not top5:
            print(f"[WARN] {p['keyword']}: 모든 종목이 다른 그룹에 배정되어 제외",
                  file=sys.stderr)
            continue
        out.append({
            "keyword": p["keyword"],
            "source_themes": p["source_themes"],
            "total_qualifying": p["total_qualifying"],
            "top5": top5,
        })
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

    # raw_scan.json 은 거른 뒤가 아니라 거르기 전을 담는다. 제외가 오늘 무엇을
    # 지웠는지 나중에 확인하려면 지워지기 전 모습이 남아 있어야 한다.
    scanned = len(raw)
    raw = filter_themes(raw)
    print(f"[filter] {scanned}개 중 {len(raw)}개 통과 "
          f"({scanned - len(raw)}개 제외)", file=sys.stderr)

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
