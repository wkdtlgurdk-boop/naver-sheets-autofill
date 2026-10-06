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
from pathlib import Path

from theme_fit import load_affinity, misfit

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://m.stock.naver.com/",
    "User-Agent": "Mozilla/5.0 naverstock-web-api-skill/1.0",
}
MIN_CAP = 2000       # 억원
MIN_RATE = 5.0        # %
MID_CAP = 1000       # 억원 — 중간 문턱. 크게 오른 중형주를 받는다
MID_RATE = 12.0       # %
SURGE_CAP = 500      # 억원 — 급등주에만 적용하는 완화 문턱
SURGE_RATE = 20.0     # %
MIN_AMOUNT = 10_000    # 백만원 = 100억. 거래대금 문턱.
MIN_COUNT = 3          # qualifying stocks needed for a theme to count
MERGE_THRESHOLD = 0.6  # overlap coefficient for auto-merge
SCAN_PAGES = 3         # 목록 전체를 읽는다 (2026-09-11 실측 266개 = 3페이지)
PAGE_SIZE = 100        # 한 페이지에 받는 테마 수 (등락률 내림차순 상위)
# 신규상장주는 어느 테마에서도 빼기 (2026-10-01 사용자 지정). 브릴스(+59%)가
# 상장 직후 수급으로 오른 것을 로봇 테마 1위로 올렸다. 상장 시기를 따로 받는
# 대신 네이버가 이미 만들어 두는 '2026 하반기 신규상장' 같은 테마 명단을 쓴다
# — 스캔이 어차피 전 테마를 읽으므로 호출이 늘지 않는다. 올해와 작년 것만.
# 하루 30% 넘게 오른 종목은 받지 않는다 (2026-10-01 사용자 지정). 가격제한폭이
# ±30% 라 상한가는 +30.00% 까지다 — 그보다 더 오른 것은 상장 첫날·거래 재개
# 처럼 제한폭이 없는 날이고, 테마가 움직인 것이 아니다 (브릴스 +59.49%).
MAX_RATE = 30.0
NEW_LISTING_TAG = "신규상장"
NEW_LISTING_YEARS = 2
PARTICIPATION_RATE = 3.0   # % — 참여율을 셀 때 "올랐다"고 보는 문턱
MIN_PARTICIPATION = 0.2    # 구성종목 중 그만큼 오른 비율의 하한

# Keyword override: any theme whose name matches (substring) one of these
# patterns gets folded into the given keyword group, taking priority over
# the automatic overlap-based merge. Edit this list, or keywords.json next to
# it, to steer future runs.
KEYWORD_PATTERNS = {
    "바이오": [
        "코로나19", "백신", "진단시약", "방역", "바이오시밀러", "제약업체",
        "비만치료제", "mRNA", "치매", "면역항암제",
    ],
    # '2차전지' 는 custom_themes.json 의 셀·소재·장비가 맡는다. 예전에는 여기서
    # '2차전지' 글자가 든 테마를 모두 한 키워드로 접어서, 직접 고른 '2차전지 소재'
    # 까지 리튬·전기차·ESS 와 한 칸으로 합쳐졌다(2026-10-06 TCC스틸·알멕).
    "리튬·폐배터리": ["리튬", "폐배터리"],
    "비철금속": ["귀금속", "비철금속"],
}

# User-editable source of truth, kept in this repo as keywords.json. It used
# to live on the user's desktop PC, which a GitHub runner cannot see. Each entry
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
            if s.get("new_listing"):
                print(f"[제외] {s['name']} @ '{name}' — 신규상장주", file=sys.stderr)
                continue
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


def fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def num(value):
    """'15,130' 이나 '29.98' 같은 문자열을 숫자로. 못 읽으면 None."""
    if value in (None, "", "N/A", "-"):
        return None
    try:
        return float(str(value).replace(",", "").replace("+", ""))
    except ValueError:
        return None


def qualifies(rate, cap, amount=None):
    """오늘 이 종목이 자기 테마를 대표할 만한가.

    **거래대금 100억이 먼저다.** 시총만 보면 하루 25억 거래된 종목이 테마
    대표로 올라온다 -- 2026-09-16 에이스테크(+29.97%, 거래대금 25억)가 그랬다.
    시총이 크다고 오늘 거래가 있었던 것은 아니고, 사람이 실제로 살 수 있는
    자리인지는 거래대금이 말한다. 아침 매수 후보 봇도 같은 문턱을 관문에 걸고
    있어서, 시트에만 올라오고 봇에서는 떨어지는 종목이 생기던 것도 같이 없어진다.

    단위는 시트와 같은 백만원이다(accumulatedTradingValue). amount 를 주지
    않으면 이 문턱을 건너뛴다 -- 옛 raw_scan.json 을 --regroup 으로 다시
    돌릴 때 그 값이 없기 때문이다.

    문턱이 둘이다. 평소에는 시총 2000억 이상이 5% 넘게 오른 경우지만,
    20% 넘게 오른 종목은 시총 500억까지 받는다.

    문턱이 셋이다. 2026-09-11 에 중간 단계를 더했다. 그 전에는 시총 2,000억
    미만은 20% 넘게 오르지 않는 한 전부 버려져서, +10~15% 구간의 1,000~2,000억
    종목이 통째로 사각지대였다. 그날 MLCC 테마가 등락률 1위(+7.69%)였는데도
    조건충족 2종목으로 탈락했다 — 아바텍(+14.02%, 1,412억)과 코칩(+10.36%,
    1,868억)이 시총에서 걸렸고, 코칩은 문턱과 132억 차이였다.

    단일 시총 문턱은 두 번 작동해서, 대표종목을 고를 때만이 아니라 테마가
    살아 있는지 셀 때도 걸린다. 9/8 광통신 테마는 5% 넘게 오른 종목이 실제로
    둘이었는데(빛과전자 +30.00%/4,102억, 한국첨단소재 +5.54%/577억) 시총이
    하나를 지워 MIN_COUNT 1/3 으로 탈락했다 — 상한가 종목을 낀 테마가
    통째로 사라진 것이다. 급등 구간에서 도는 중소형주가 그날 시장이 실제로
    반응한 곳이므로, 그 구간에서만 시총을 낮춰 센다.
    """
    if amount is not None and amount < MIN_AMOUNT:
        return False
    if rate > MAX_RATE:
        return False
    if rate >= SURGE_RATE and cap >= SURGE_CAP:
        return True
    if rate >= MID_RATE and cap >= MID_CAP:
        return True
    return rate >= MIN_RATE and cap >= MIN_CAP


def fetch_theme_list():
    """테마 목록을 등락률 내림차순으로. 각 항목이 테마의 오늘 성적을 들고 온다.

    2026-09-11 에 네이버가 finance.naver.com 의 테마 화면을 Next.js 기반으로
    갈아치우면서 옛 HTML 파싱이 통째로 죽었다. 새 경로는 같은 데이터를 JSON 으로
    주는데, 오히려 우리가 종목을 하나하나 세서 만들던 값들이 이미 들어 있다:

        no / name          테마 번호와 이름 (번호는 옛것과 같다)
        totalCount         구성 종목 수
        riseCount          그중 오른 종목 수   -> 참여율을 셀 필요가 없다
        changeRate         테마 전체 등락률

    목록은 등락률 내림차순이지만 그 순서로 자르면 안 된다. 2026-09-11 실측:
    조건을 충족한 테마 8개 중 4개가 100위 밖이었다 — 전기차(142위, -0.42%),
    밸류업(151위), 반도체 재료/부품(211위), 2차전지(214위, -1.10%).

    테마 평균 등락률과 '그 안에 급등주가 3개 있느냐'는 별개이기 때문이다.
    구성 종목이 수십 개인 테마는 평균이 마이너스여도 그 안에 조건을 넘긴
    대형주가 셋 있을 수 있다. 평균은 구성종목이 많을수록 희석되므로, 등락률로
    자르면 크고 무거운 테마가 통째로 잘린다. 그래서 전부 읽는다.
    """
    seen, order = set(), []
    for page in range(1, SCAN_PAGES + 1):
        url = (f"https://m.stock.naver.com/api/stocks/theme"
               f"?page={page}&pageSize={PAGE_SIZE}")
        groups = fetch_json(url).get("groups") or []
        new = [g for g in groups if g.get("no") not in seen]
        if not new:
            break
        for g in new:
            seen.add(g["no"])
            order.append(g)
        time.sleep(0.12)
    return order


def fetch_theme_stocks(theme_no):
    """테마 하나의 구성 종목. 등락률·시총·거래대금을 그대로 준다.

    단위는 옛 HTML 과 같다 — marketValue 는 억원, accumulatedTradingValue 는
    백만원이다. 그래서 qualifies() 의 문턱도 그대로 쓸 수 있다.
    """
    url = (f"https://m.stock.naver.com/api/stocks/theme/{theme_no}"
           f"?page=1&pageSize={PAGE_SIZE}")
    out = []
    for s in fetch_json(url).get("stocks") or []:
        code = s.get("itemCode")
        name = s.get("stockName")
        rate = num(s.get("fluctuationsRatio"))
        cap = num(s.get("marketValue"))
        amount = num(s.get("accumulatedTradingValue"))
        if not code or not name or None in (rate, cap, amount):
            continue
        out.append({"name": name, "code": code, "rate": rate,
                    "cap": int(cap), "amount": int(amount)})
    return out


def eok(text):
    """'1,604조 8,035억' / '456억' / '-' -> 억원(float). 못 읽으면 None."""
    text = str(text or "").replace(",", "").replace(" ", "")
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)조)?(?:(\d+(?:\.\d+)?)억)?", text)
    if not text or not m or not (m.group(1) or m.group(2)):
        return None
    return float(m.group(1) or 0) * 10000 + float(m.group(2) or 0)


def fetch_stock_by_code(code):
    """테마에 안 속한 종목 하나의 시세를 코드로. 테마 행과 같은 모양·단위.

    basic 이 등락률을, integration 의 totalInfos 가 시총·거래대금을 글자로 준다
    (2026-10-01 러너 실측: '대금' '5조 6,097억', '시총' '456억'). 시총은 억원,
    거래대금은 테마 행과 맞춰 백만원으로 바꾼다. 하나라도 못 읽으면 None.
    """
    basic = fetch_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
    info = fetch_json(f"https://m.stock.naver.com/api/stock/{code}/integration")
    rows = {i.get("code"): i.get("value") for i in info.get("totalInfos") or []}
    rate = num(basic.get("fluctuationsRatio"))
    cap = eok(rows.get("marketValue"))
    amount = eok(rows.get("accumulatedTradingValue"))
    if rate is None or cap is None:
        return None
    return {"name": basic.get("stockName") or info.get("stockName") or code, "code": code,
            "rate": rate, "cap": int(cap), "amount": int((amount or 0) * 100)}


CUSTOM_FILE = Path(__file__).with_name("custom_themes.json")


def load_custom_themes(path=None):
    """custom_themes.json 의 테마 목록. 없거나 깨졌으면 빈 목록."""
    try:
        doc = json.loads((path or CUSTOM_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [t for t in doc.get("themes") or [] if t.get("name") and t.get("stocks")]


def _base(name):
    return re.sub(r"\([^)]*\)", "", name or "").strip().lower()


def custom_theme_entries(custom, seen, fetch=None):
    """직접 만든 테마를 네이버 테마와 같은 모양으로. seen 은 {종목명: 시세}.

    시세는 먼저 스캔이 네이버 테마 266개를 읽으며 이미 본 종목에서 꺼낸다.
    어느 네이버 테마에도 없는 종목은 테마의 codes 에 코드를 적어 두면 그 코드로
    따로 받는다(fetch_stock_by_code, 종목당 두 번 호출). 그래도 없으면 못 찾은
    것으로 찍는다.
    """
    fetch = fetch or fetch_stock_by_code
    out = []
    for t in custom:
        codes = t.get("codes") or {}
        for n in t["stocks"]:
            if n not in seen and codes.get(n):
                try:
                    got = fetch(codes[n])
                except Exception as exc:                  # noqa: BLE001
                    print(f"[직접테마] {n}({codes[n]}) 코드 조회 실패: {exc}", file=sys.stderr)
                    got = None
                if got:
                    got["name"] = n
                    seen[n] = got
        stocks = [seen[n] for n in t["stocks"] if n in seen]
        missing = [n for n in t["stocks"] if n not in seen]
        if missing:
            print(f"[직접테마] {t['name']} — 시세를 못 찾은 종목: {', '.join(missing)}",
                  file=sys.stderr)
        qualifying = [s for s in stocks if qualifies(s["rate"], s["cap"], s.get("amount"))]
        if len(qualifying) < MIN_COUNT:
            continue
        out.append({
            "theme_name": t["name"],
            "theme_id": "custom:" + t["name"],
            "member_count": len(stocks),
            "members": stocks,
            "up_count": sum(1 for s in stocks if s["rate"] >= PARTICIPATION_RATE),
            "avg_rate": round(sum(s["rate"] for s in stocks) / len(stocks), 2) if stocks else None,
            "qualifying": qualifying,
        })
    return out


def is_recent_listing_theme(name, year):
    """'2026 하반기 신규상장' 처럼 올해·작년의 신규상장 묶음인가. 연도가 없으면 그렇다고 본다."""
    if NEW_LISTING_TAG not in (name or ""):
        return False
    m = re.search(r"(20\d\d)", name)
    return not m or int(m.group(1)) > year - NEW_LISTING_YEARS


def mark_new_listings(results, codes):
    """신규상장 명단에 든 종목에 new_listing 표시를 단다. raw_scan.json 에 남아
    --regroup 때도 같은 판단이 나온다."""
    for t in results:
        for key in ("members", "qualifying"):
            for s in t.get(key) or []:
                if s.get("code") in codes:
                    s["new_listing"] = True


def scan_themes():
    themes = fetch_theme_list()
    print(f"themes listed: {len(themes)}", file=sys.stderr)
    # 목록이 0개인 건 '오늘 오른 테마가 없다'가 아니라 목록을 못 읽었다는 뜻이다.
    # 조용히 0개로 진행하면 그날 시트가 빈 채로 남고 아무도 이유를 모른다.
    if not themes:
        raise SystemExit(
            "[중단] 테마 목록이 0개입니다. 네이버가 예상과 다른 응답을 줬습니다 "
            "(점검·차단·API 변경). 스캔을 신뢰할 수 없어 멈춥니다.")

    # 직접 만든 테마가 대신하는 네이버 테마는 건너뛴다 (custom_themes.json).
    # 괄호를 뗀 이름이 같거나, replaces 에 적은 글자가 이름에 들어 있으면 대신한다
    # — '2차전지 소재'처럼 이름이 달라도 네이버 '2차전지(소재/부품)'를 밀어낼 수 있게.
    custom = load_custom_themes()
    replaced = {_base(t["name"]) for t in custom}
    replace_parts = [p for t in custom for p in (t.get("replaces") or []) if p]
    seen = {}
    results = []
    new_codes = set()
    year = time.localtime().tm_year
    for t in themes:
        name = t.get("name") or str(t.get("no"))
        stocks = fetch_theme_stocks(t["no"])
        for s in stocks:
            seen.setdefault(s["name"], s)
        if is_recent_listing_theme(name, year):
            new_codes.update(s["code"] for s in stocks)
        if _base(name) in replaced or any(p in name for p in replace_parts):
            print(f"[직접테마] 네이버 '{name}' 는 직접 만든 테마로 대신합니다", file=sys.stderr)
            continue
        qualifying = [s for s in stocks
                      if qualifies(s["rate"], s["cap"], s.get("amount"))]
        if len(qualifying) >= MIN_COUNT:
            results.append({
                "theme_name": name,
                "theme_id": str(t["no"]),
                # 참여율은 구성종목의 등락률로 직접 센다. 목록이 주는 riseCount
                # 는 '조금이라도 오른' 종목이라 너무 관대하다 — 2026-09-11 실측
                # 으로 266개 중 225개(85%)가 통과해 거르는 구실을 못 했다.
                "member_count": len(stocks),
                # 구성종목 전체. qualifying 은 '오늘 시트에 오를 만큼 오른' 것만
                # 남기는데, 같은 테마에 속하면서 그날 덜 오른 종목이 따로 다른
                # 신호(수급·거래량·임원매수)에 걸리는 일이 있다. 그때 '이 종목이
                # 오늘 뜬 테마 소속인가'를 물으려면 명단이 있어야 한다.
                "members": stocks,
                "up_count": sum(1 for s in stocks
                                if s["rate"] >= PARTICIPATION_RATE),
                "avg_rate": num(t.get("changeRate")),
                "qualifying": qualifying,
            })
        time.sleep(0.12)
    results += custom_theme_entries(custom, seen)
    mark_new_listings(results, new_codes)
    print(f"[신규상장] 명단 {len(new_codes)}종목은 테마 종목에서 뺍니다", file=sys.stderr)
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


NARROW_RATIO = 0.5   # 구성종목이 가장 큰 테마의 이 비율 이하면 '좁은 테마'


def _base_name(theme_name):
    return re.sub(r"\([^)]*\)", "", theme_name or "").strip()


def core_codes(themes):
    """병합된 그룹에서 먼저 채울 종목 -- 좁은 테마에서 조건을 넘은 종목들.

    2026-09-28 '의료AI·의료기기' 는 의료AI(좁다)와 의료기기(110종목)를 합친
    그룹이었다. 상승률 순으로만 다섯을 뽑으니 HLB 신약 허가로 바이오가 통째로
    뜬 날 한국비엔씨·동국생명과학·레메디가 자리를 차지했고, 같은 날 오른
    루닛·셀바스헬스케어·딥노이드는 빠졌다. 그룹 이름이 말하는 것은 좁은 쪽이다.

    좁은 테마 = 구성종목이 가장 큰 테마의 NARROW_RATIO 이하이고, 괄호를 뗀
    이름이 그 큰 테마와 다른 것. '2차전지(전고체)' 는 '2차전지' 의 갈래라
    좁아도 먼저 채우지 않는다 -- 그랬다가는 전고체 종목이 더 크게 오른
    2차전지 대장주를 밀어낸다.

    좁은 쪽 종목이 MIN_COUNT 이상이면 그 종목만 쓴다(칸이 비어도 둔다).
    9/28 재실행에서 의료AI 종목은 넷뿐이었고, 남은 한 칸을 HLB 로 오른
    한국비엔씨가 채웠다 -- 바로 걸러내려던 종목이다. 좁은 쪽이 MIN_COUNT
    에 못 미치면 그때만 넓은 쪽으로 채운다.
    """
    sized = [t for t in themes if t.get("member_count")]
    if len(sized) < 2:
        return set()
    broad = max(sized, key=lambda t: t["member_count"])
    narrow = [t for t in sized
              if t["member_count"] <= broad["member_count"] * NARROW_RATIO
              and _base_name(t["theme_name"]) != _base_name(broad["theme_name"])]
    return {s["code"] for t in narrow for s in t["qualifying"]}


def custom_homes(custom=None):
    """{종목명: {직접테마 이름...}}. 한 종목이 둘 이상의 직접테마에 들 수 있다."""
    homes = {}
    for t in (load_custom_themes() if custom is None else custom):
        for n in t["stocks"]:
            homes.setdefault(n, set()).add(t["name"])
    return homes


def group_themes(raw):
    """겹침 병합과 키워드 접기. 직접 만든 테마는 둘 다 거치지 않고 제 이름으로 남는다.

    직접 테마는 사용자가 나눈 칸이다. 리튬·전기차와 종목이 겹친다고 합치거나
    '2차전지' 같은 키워드로 접으면 나눈 의미가 사라진다 — 2026-10-06 묶기(LLM)가
    인증 실패로 건너뛰어지자 '2차전지 소재'가 리튬·전기차·ESS 와 한 칸이 됐다.
    """
    custom = [t for t in raw if str(t.get("theme_id", "")).startswith("custom:")]
    naver = [t for t in raw if not str(t.get("theme_id", "")).startswith("custom:")]
    groups = apply_keyword_overrides(merge_by_overlap(naver)) if naver else []
    return groups + [{"keyword": t["theme_name"], "themes": [t]} for t in custom]


def build_output(final_groups, affinity=None):
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
        member_map = {}
        source_names = []
        for t in g["themes"]:
            source_names.append(t["theme_name"])
            for s in t["qualifying"]:
                stock_map[s["code"]] = s
            # 병합된 테마들의 구성종목은 합집합으로 둔다. 겹쳐서 하나로 묶인
            # 테마라면 어느 쪽 소속이든 같은 키워드 아래 있는 것이 맞다.
            for s in t.get("members") or []:
                member_map[s["code"]] = s
        # 좁은 테마 종목을 먼저, 그 안에서 상승률 순 (core_codes 머리말).
        core = core_codes(g["themes"])
        if len(g["themes"]) > 1:
            sizes = ", ".join(f"{t['theme_name']} {t.get('member_count')}종목/조건 {len(t['qualifying'])}"
                              for t in g["themes"])
            print(f"[좁은테마] {g['keyword']} — {sizes}; 먼저 채울 {len(core)}개: "
                  + ", ".join(s["name"] for s in stock_map.values() if s["code"] in core),
                  file=sys.stderr)
        pools.append({
            "keyword": g["keyword"],
            "source_themes": sorted(set(source_names)),
            "total_qualifying": len(stock_map),
            "members": sorted(member_map.values(), key=lambda s: -s["rate"]),
            "ranked": sorted((s for s in stock_map.values()
                              if len(core) < MIN_COUNT or s["code"] in core),
                             key=lambda s: (s["code"] not in core, -s["rate"])),
        })

    pools.sort(key=lambda p: -max((s["rate"] for s in p["ranked"]), default=0))

    # 1년치 시트 기록으로 본 오배정. 삼성전자가 반도체로 오른 날 '자율주행차'
    # 칸의 대장주가 되는 것을 막는다(theme_fit 머리말). 빠진 종목은 제 계열
    # 그룹이 뒤에서 가져갈 수 있다.
    affinity = load_affinity() if affinity is None else affinity

    # 직접 고른 테마(custom_themes.json)의 종목은 그 테마가 오늘 떴으면 그 칸으로만
    # 간다. 사용자가 시트 기록으로 나눠 둔 자리이므로, 네이버 테마가 같은 종목을
    # 다른 칸으로 끌고 가지 못하게 한다(10/6 나노신소재가 '태양광에너지' 칸에).
    home = custom_homes()
    present = {p["keyword"] for p in pools}

    taken, out = set(), []
    for p in pools:
        top5 = []
        for s in p["ranked"]:
            if s["code"] in taken:
                continue
            mine = home.get(s["name"], set())
            if mine and p["keyword"] not in mine and mine & present:
                print(f"[직접테마 소속] {p['keyword']} — {s['name']} {s['rate']:+.2f}% 뺌: "
                      f"{', '.join(sorted(mine & present))} 칸 종목", file=sys.stderr)
                continue
            why = misfit(s["name"], [p["keyword"]] + p["source_themes"], affinity)
            if why:
                print(f"[오배정] {p['keyword']} — {s['name']} {s['rate']:+.2f}% 뺌: {why}",
                      file=sys.stderr)
                continue
            taken.add(s["code"])
            top5.append(s)
            if len(top5) == 5:
                break
        # 배정 뒤에 다시 센다. MIN_COUNT 는 병합 전 테마마다 보는 값이라,
        # 앞 그룹이 종목을 먼저 가져가면 여기서 3개 밑으로 내려앉는다 --
        # 2026-09-16 에 철도·디스플레이폰부품·2차전지가 나란히 2종목으로
        # 시트에 올라갔다. '조건 충족 3개 이상인 테마'라고 해 놓고 2개짜리
        # 슬롯을 내보내면 그 말이 아니게 된다.
        if len(top5) < MIN_COUNT:
            taken -= {s["code"] for s in top5}   # 뒤 그룹이 다시 쓸 수 있게 돌려준다
            print(f"[제외] {p['keyword']} — 배정 후 {len(top5)}종목 "
                  f"(MIN_COUNT {MIN_COUNT}). 앞 그룹이 먼저 가져갔습니다.",
                  file=sys.stderr)
            continue
        out.append({
            "keyword": p["keyword"],
            "source_themes": p["source_themes"],
            "total_qualifying": p["total_qualifying"],
            # top5 는 시트에 오르는 대장주, members 는 테마 소속 전체다.
            # 둘을 갈라 두는 이유: 시트는 사람이 읽는 표라 다섯 줄이면 족하고,
            # 봇은 '소속인가'를 물어야 해서 전부가 필요하다.
            "members": [{"name": s["name"], "code": s["code"], "rate": s["rate"]}
                        for s in p["members"]],
            # 뽑는 순서는 좁은 테마 먼저지만, 시트와 카드에는 상승률 순으로 적는다.
            "top5": sorted(top5, key=lambda s: -s["rate"]),
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

    final_groups = group_themes(raw)
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
