#!/usr/bin/env python3
"""final_groups.json 을 스레드용 카드 이미지로 그린다.

예전 카드 봇은 사용자 PC 안에만 있었고, 그 PC 가 그래픽카드 고장으로 켜지지
않게 되면서 코드가 통째로 사라졌다(리포에는 결과 PNG 만 올라와 있었다).
그래서 남아 있는 2026-09-04/07/08 카드를 픽셀 단위로 재서 다시 그린다 —
1080x1350, 여백 68, 색·행간격·글자크기 전부 그 이미지에서 뽑은 값이다.

카드는 두 종류다.
  summary_card : 하루 요약. 지수·거래대금·외인 + 테마 순위표.
  theme_card   : 테마 하나. 평균상승률·거래대금 + 상위 5종목 + 묶은 테마.

숫자는 전부 final_groups.json / market_summary.json 에서 나온다. 시트가
계산하는 값(대표행 AVERAGEA/SUM)과 같은 정의를 쓰되, 시트를 읽지 않는다 —
카드는 시트 쓰기가 실패한 날에도 나와야 하고, 그 둘이 같은 원본에서 나오면
어긋날 일이 없다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import theme_scan as ts
from as_of import as_of

KST = timezone(timedelta(hours=9))
W, H = 1080, 1350
M = 68                      # 좌우 여백. 본문 오른쪽 끝은 W - M - 1 = 1011.
R = W - M - 1

# 원본 카드에서 뽑은 색. 배경이 순검정이 아니라 살짝 따뜻한 쪽이다.
BG      = (20, 16, 15)
PANEL   = (27, 22, 20)      # '묶은 테마' 박스
INK     = (245, 240, 234)   # 본문 흰색
SOFT    = (183, 172, 162)   # 박스 안 본문
MUTED   = (140, 131, 123)   # 라벨·부제
DIM     = (110, 101, 94)    # 푸터
DATE    = (160, 150, 141)   # 헤더 날짜
RED     = (255, 90, 78)     # 상승률
BLUE    = (90, 150, 255)    # 하락률 (수급 카드에는 내린 종목도 나온다)
GOLD    = (232, 182, 76)    # 배지, 1위
RULE_H  = (50, 42, 38)      # 헤더 아래 (2px)
RULE_S  = (36, 30, 27)      # 섹션 구분 (2px)
RULE_R  = (33, 28, 25)      # 행 구분 (1px)

FONT_DIR = Path("/usr/share/fonts/opentype/noto")
WEIGHTS = {"black": "NotoSansCJK-Black.ttc", "bold": "NotoSansCJK-Bold.ttc",
           "medium": "NotoSansCJK-Medium.ttc", "regular": "NotoSansCJK-Regular.ttc"}
_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

# 글자 크기. 원본 카드의 각 줄 높이를 재서 맞춘 값이라 한 곳에 모아 둔다 —
# 폰트를 바꾸면 같은 크기라도 줄 높이가 달라지므로 여기만 다시 맞추면 된다.
SZ = {
    "date": 30, "badge": 27, "title": 73, "sub": 26, "footer": 21,
    "stat_label": 24, "stat_value": 46,
    "row_name": 36, "row_rate": 36, "row_amt": 31,
    "box_label": 21, "box_body": 23,
    "idx_label": 28, "idx_value": 68, "idx_sub": 24,
    "m_rank": 28, "m_name": 34, "m_rate": 34, "m_amt": 28,
}


def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    key = (weight, size)
    if key not in _cache:
        path = FONT_DIR / WEIGHTS[weight]
        if not path.exists():                      # Black/Medium 은 -extra 패키지에만 있다
            path = FONT_DIR / WEIGHTS["bold" if weight in ("black", "bold") else "regular"]
        _cache[key] = ImageFont.truetype(str(path), size)
    return _cache[key]


WEEKDAY = "월화수목금토일"


def header_date(now: datetime) -> str:
    return f"{now:%Y.%m.%d} ({WEEKDAY[now.weekday()]})"


def no_theme_day() -> bool:
    """주도 테마가 없는 날인가 (scan_mode.json). 그날은 '오늘의 테마 없음' 한 장만 만든다.
    '오늘의 수급'(외국인·기관 순매수 상위)은 테마와 상관없이 매일 따로 만든다(--supply)."""
    return ts.no_theme_mode(Path(__file__).parent)


SUPPLY_DIR = "supply"    # 그 날 카드 폴더 아래 '오늘의 수급' 카드 자리
SUPPLY_FOOTER = (f"외국인·기관 순매수 상위 {ts.SUPPLY_TOP}종목 · 코스피·코스닥 합산, "
                 "순매수 금액순, ETF 제외")
SUPPLY_BADGE = "수급"   # 수급 카드 배지. '1 / 2'·'MONTHLY' 대신 (10/22 사용자 지정)


def footer_text(mode: dict | None = None) -> str:
    """조건을 카드 밑에 적어 둔다. theme_scan 의 상수에서 만들어야 낡지 않는다.
    기존 기준으로 테마가 없어 완화 기준으로 돈 날은 scan_mode.json 의 낮춘 문턱을 적는다.
    mode 를 주면 scan_mode.json 대신 그것을 쓴다 (시험용)."""
    if mode is None:
        mode = ts.load_mode(Path(__file__).parent)
    # 테마 없는 날에도 테마 카드 하단은 '이 조건을 채운 테마가 없었다'는 뜻으로 같은 문턱을 적는다.
    ts.apply_mode(mode)
    prefix = "완화 기준 · " if mode.get("relaxed") else ""
    return (f"{prefix}시가총액 {ts.MIN_CAP:,}억↑·상승률 {ts.MIN_RATE:g}%↑ "
            f"({ts.MID_RATE:g}%↑는 {ts.MID_CAP:,}억↑, {ts.SURGE_RATE:g}%↑는 "
            f"{ts.SURGE_CAP:,}억↑) 종목 {ts.MIN_COUNT}개 이상인 테마")


def eok(amount_mn: float) -> int:
    """거래대금 백만원 -> 억원. final_groups.json 은 백만 단위로 담고 있다."""
    return round(amount_mn / 100)


def tone(rate: float):
    """오르면 빨강, 내리면 파랑. 테마 카드는 늘 빨강이지만 수급 카드엔 내린 종목이 있다."""
    return RED if rate >= 0 else BLUE


def avg_rate(stocks: list[dict]) -> float:
    return sum(s["rate"] for s in stocks) / len(stocks) if stocks else 0.0


def spaced(keyword: str) -> str:
    """제목에서만 가운뎃점 양옆을 띄운다 — 원본 카드가 그렇게 쓴다."""
    return keyword.replace("·", " · ")


class Card:
    def __init__(self, now: datetime, badge: str, footer: str | None = None):
        self.im = Image.new("RGB", (W, H), BG)
        self.d = ImageDraw.Draw(self.im)
        self._header(now, badge)
        self._footer(footer)

    # --- 조각들 -------------------------------------------------------
    def _header(self, now: datetime, badge: str):
        self.d.text((M, 97), header_date(now), font=font("bold", SZ["date"]),
                    fill=DATE, anchor="lm")
        f = font("black", SZ["badge"])
        w = self.d.textlength(badge, font=f)
        x0, x1 = R - w - 30, R
        self.d.rounded_rectangle([x0, 74, x1, 120], radius=23, fill=GOLD)
        self.d.text(((x0 + x1) / 2, 98), badge, font=f, fill=BG, anchor="mm")
        self.d.rectangle([M, 147, R, 148], fill=RULE_H)

    def _footer(self, text: str | None = None):
        self.d.rectangle([M, 1236, R, 1237], fill=RULE_S)
        self.d.text((M, 1275), text or footer_text(), font=font("regular", SZ["footer"]),
                    fill=DIM, anchor="lm")

    def title(self, text: str, sub: str):
        # 「반도체 밸류체인(HBM · 장비 · 시스템반도체)」 같은 긴 그룹 이름이 카드
        # 오른쪽 끝을 넘어 잘린 날이 있었다(10/2). 들어갈 때까지 글자를 줄인다.
        f, text = shrink(self.d, text, "black", SZ["title"], R - M, MIN_TITLE)
        self.d.text((M, 258), text, font=f, fill=INK, anchor="ls")
        self.d.text((M, 329), sub, font=font("medium", SZ["sub"]), fill=MUTED, anchor="ls")

    def row_rule(self, y: int, thick: int = 1):
        self.d.rectangle([M, y, R, y + thick - 1], fill=RULE_R if thick == 1 else RULE_S)

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.im.save(path, "PNG", optimize=True)
        return path


def theme_card(now: datetime, group: dict, rank: int, total: int, *, sub: str | None = None,
               amount_label: str = "거래대금", box_label: str = "묶은 테마",
               footer: str | None = None, show_rate: bool = True,
               badge: str | None = None, show_total: bool = True) -> Card:
    """테마 한 장. rank 는 거래대금 순위(배지의 '1 / 7').
    '오늘의 수급' 카드도 이 판을 쓴다 — 금액 칸이 거래대금 대신 순매수고,
    상승률은 적지 않는다(show_rate=False, 10/15 사용자 지정). 수급은 10종목이라
    (10/22) 6종목 이상이면 행 간격·글자를 줄이고 아래 상자를 뺀다. 수급 카드는 합계도
    적지 않는다(show_total=False, 10/22 사용자 지정) -- 그 자리만큼 종목 줄을 올린다."""
    c = Card(now, badge or f"{rank} / {total}", footer)
    top = group["top5"]
    c.title(spaced(group["keyword"]),
            sub or f"조건 충족 {group['total_qualifying']}종목 중 상위 {len(top)}개")

    if not show_total:
        c.d.rectangle([M, 375, R, 376], fill=RULE_S)
        y = 446
        for i, s in enumerate(top):
            c.d.text((M, y), s["name"], font=font("bold", 34),
                     fill=GOLD if i == 0 else INK, anchor="ls")
            c.d.text((1008, y), f"{eok(s['amount']):,}억", font=font("black", 34),
                     fill=INK, anchor="rs")
            if i < len(top) - 1:
                c.row_rule(y + 26)
            y += 74
        return c

    # 요약 두 칸: 평균상승률 / 거래대금 (수급 카드는 금액 한 칸)
    ax = 378
    if show_rate:
        c.d.text((M, 398), "평균상승률", font=font("medium", SZ["stat_label"]), fill=MUTED, anchor="ls")
        c.d.text((M, 455), f"{avg_rate(top):+.2f}%", font=font("black", SZ["stat_value"]),
                 fill=tone(avg_rate(top)), anchor="ls")
    else:
        ax = M
    c.d.text((ax, 398), amount_label, font=font("medium", SZ["stat_label"]), fill=MUTED, anchor="ls")
    c.d.text((ax, 455), f"{sum(eok(s['amount']) for s in top):,}억",
             font=font("black", SZ["stat_value"]), fill=INK, anchor="ls")
    c.d.rectangle([M, 515, R, 516], fill=RULE_S)

    # 종목 5행. 1위만 금색 — 원본이 그렇다. 6행 이상(수급 10종목)은 촘촘하게.
    dense = len(top) > 5
    step, gap = (60, 22) if dense else (92, 32)
    name_sz, num_sz = (32, 32) if dense else (SZ["row_name"], SZ["row_rate"])
    y = 576 if dense else 586
    for i, s in enumerate(top):
        c.d.text((M, y), s["name"], font=font("bold", name_sz),
                 fill=GOLD if i == 0 else INK, anchor="ls")
        if show_rate:
            c.d.text((813, y), f"{s['rate']:+.2f}%", font=font("black", SZ["row_rate"]),
                     fill=tone(s["rate"]), anchor="rs")
            c.d.text((1008, y), f"{eok(s['amount']):,}억", font=font("medium", SZ["row_amt"]),
                     fill=MUTED, anchor="rs")
        else:
            c.d.text((1008, y), f"{eok(s['amount']):,}억", font=font("black", num_sz),
                     fill=INK, anchor="rs")
        if i < len(top) - 1:
            c.row_rule(y + gap)
        y += step
    if dense:
        return c       # 10행이면 상자 자리가 없다. 기준은 부제와 바닥글에 있다.

    # 묶은 테마 — 이 그룹이 네이버의 어느 테마들을 합친 것인지 밝힌다.
    c.d.rounded_rectangle([M, 1057, R, 1160], radius=14, fill=PANEL)
    c.d.text((M + 25, 1098), box_label, font=font("medium", SZ["box_label"]),
             fill=MUTED, anchor="ls")
    c.d.text((M + 25, 1135), fit(c.d, " · ".join(group["source_themes"]),
                                 font("medium", SZ["box_body"]), R - M - 50),
             font=font("medium", SZ["box_body"]), fill=SOFT, anchor="ls")
    return c


def summary_card(now: datetime, groups: list[dict], market: dict, *,
                 title: str = "오늘의 테마", sub: str | None = None,
                 footer: str | None = None, show_rate: bool = True,
                 badge: str = "MONTHLY") -> Card:
    c = Card(now, badge, footer)
    c.title(title, sub or f"조건 충족 테마 {len(groups)}개 · 거래대금 순")

    for x, key, label in ((M, "KOSPI", "KOSPI"), (564, "KOSDAQ", "KOSDAQ")):
        m = market[key]
        c.d.text((x, 400), label, font=font("bold", SZ["idx_label"]), fill=MUTED, anchor="ls")
        c.d.text((x, 480), f"{round(m['index']):,}", font=font("black", SZ["idx_value"]),
                 fill=INK, anchor="ls")
        f = font("medium", SZ["idx_sub"])
        # 장 시작 전이나 지난 날짜 재작성에서는 거래대금이 안 온다. 그때
        # "None조" 를 찍느니 값이 없다고 말하는 편이 낫다 -- 카드는 공개된다.
        amt = m.get("amount_trillion")
        head = f"거래대금 {amt}조 · 외인 " if amt is not None else "거래대금 — · 외인 "
        c.d.text((x, 518), head, font=f, fill=MUTED, anchor="ls")
        won = m.get("foreign_eok") or 0
        c.d.text((x + c.d.textlength(head, font=f), 518), f"{won:+,}억",
                 font=font("bold", SZ["idx_sub"]), fill=RED if won >= 0 else BLUE,
                 anchor="ls")
    c.d.rectangle([517, 378, 518, 522], fill=RULE_S)
    c.d.rectangle([M, 555, R, 556], fill=RULE_S)

    y = 615
    for i, g in enumerate(groups):
        amt = f"{sum(eok(s['amount']) for s in g['top5']):,}억"
        rate_f = font("black", SZ["m_rate"])
        if not show_rate:
            # 수급 요약: 순위 번호도 합계도 없이(외인·기관은 순위가 아니다 -- 'TOP2' 로
            # 읽혔다, 10/22) 칸 이름과 그 칸 1위 종목만 적는다.
            lead = f"1위 {g['top5'][0]['name']}" if g["top5"] else ""
            lead_f = font("medium", SZ["m_amt"])
            room = 1008 - c.d.textlength(lead, font=lead_f) - NAME_GAP - M
            name_f, name = shrink(c.d, g["keyword"], "bold", SZ["m_name"], room, MIN_M_NAME)
            c.d.text((M, y), name, font=name_f, fill=INK, anchor="ls")
            c.d.text((1008, y), lead, font=lead_f, fill=MUTED, anchor="rs")
            if i < len(groups) - 1:
                c.row_rule(y + 23)
            y += 72
            continue
        c.d.text((M, y), str(i + 1), font=font("medium", SZ["m_rank"]),
                 fill=GOLD if i == 0 else MUTED, anchor="ls")
        rate = f"{avg_rate(g['top5']):+.2f}%"
        # 이름은 상승률 숫자 왼쪽까지만 쓴다. 10/2 요약 카드에서 긴 이름이
        # 상승률 위에 겹쳐 찍혔다 — 넘치면 글자를 줄이고, 그래도 넘치면 자른다.
        room = 814 - c.d.textlength(rate, font=rate_f) - NAME_GAP - 148
        name_f, name = shrink(c.d, g["keyword"], "bold", SZ["m_name"], room, MIN_M_NAME)
        c.d.text((148, y), name, font=name_f, fill=GOLD if i == 0 else INK, anchor="ls")
        c.d.text((814, y), rate, font=rate_f, fill=tone(avg_rate(g['top5'])), anchor="rs")
        c.d.text((1008, y), amt, font=font("medium", SZ["m_amt"]), fill=MUTED, anchor="rs")
        if i < len(groups) - 1:
            c.row_rule(y + 23)
        y += 72
    return c


def no_theme_card(now: datetime, market: dict) -> Card:
    """주도 테마가 없는 날의 '오늘의 테마' 한 장. 지수 칸은 평소와 같고 순위표 자리에 '없음'."""
    c = summary_card(now, [], market, sub="조건 충족 테마 0개 · 완화 기준까지 확인")
    c.d.text((W // 2, 800), "오늘은 주도 테마가 없습니다", font=font("black", 60),
             fill=INK, anchor="mm")
    c.d.text((W // 2, 880), "조건을 채운 테마가 한 곳도 나오지 않았습니다",
             font=font("medium", SZ["box_body"]), fill=MUTED, anchor="mm")
    c.d.text((W // 2, 940), "외국인·기관 순매수 상위는 '오늘의 수급' 글에서",
             font=font("medium", SZ["box_body"]), fill=GOLD, anchor="mm")
    return c


def supply_view(group: dict) -> dict:
    """순매수 칸을 카드 판에 맞춘다: 종목 금액 칸에 거래대금 대신 순매수(백만원)를 넣는다."""
    top = [dict(s, amount=s.get("net_buy", s.get("amount", 0))) for s in group["top5"]]
    return dict(group, top5=top)


def build_no_theme(out_dir: Path, market: dict, now: datetime) -> list[Path]:
    """테마 없는 날: <날짜>/01_monthly.png 한 장(오늘의 테마 없음)."""
    return [no_theme_card(now, market).save(out_dir / f"{now:%Y%m%d}" / "01_monthly.png")]


def build_supply(out_dir: Path, supply: list[dict], market: dict, now: datetime) -> list[Path]:
    """'오늘의 수급': <날짜>/supply/ 아래 요약 + 외국인·기관 카드. 금액은 순매수, 상승률은 없다."""
    day = out_dir / f"{now:%Y%m%d}"
    groups = [supply_view(g) for g in supply]
    made = []
    made.append(summary_card(now, groups, market, title="오늘의 수급",
                             sub=f"외국인·기관 순매수 상위 {ts.SUPPLY_TOP}종목",
                             footer=SUPPLY_FOOTER, show_rate=False, badge=SUPPLY_BADGE)
                .save(day / SUPPLY_DIR / "01_summary.png"))
    for i, g in enumerate(groups, 1):
        safe = "".join(ch for ch in g["keyword"] if ch.isalnum())
        made.append(theme_card(now, g, i, len(groups),
                               sub=f"코스피·코스닥 순매수 금액 상위 {len(g['top5'])}종목",
                               amount_label="순매수", box_label="기준",
                               footer=SUPPLY_FOOTER, show_rate=False, badge=SUPPLY_BADGE,
                               show_total=False)
                    .save(day / SUPPLY_DIR / f"{i + 1:02d}_{safe}.png"))
    return made


NAME_GAP = 28        # 요약 카드에서 테마 이름과 상승률 사이 최소 간격
MIN_TITLE = 48       # 제목은 이 크기까지만 줄인다 (원래 73)
MIN_M_NAME = 26      # 요약 카드 테마 이름 (원래 34)


def shrink(draw, text: str, weight: str, size: int, width: float, min_size: int):
    """width 안에 들어갈 때까지 글자 크기를 1씩 줄인다. min_size 에서도 넘치면 뒤를 자른다.

    대부분의 날은 원래 크기 그대로 나온다 — 원본 카드 실측 좌표(test_cards)를 깨지 않는다.
    """
    for s in range(size, min_size - 1, -1):
        f = font(weight, s)
        if draw.textlength(text, font=f) <= width:
            return f, text
    f = font(weight, min_size)
    return f, fit(draw, text, f, int(width))


def fit(draw, text: str, f, width: int) -> str:
    """한 줄에 안 들어가면 뒤를 자르고 …. 묶은 테마가 열 개씩 되는 날이 있다."""
    if draw.textlength(text, font=f) <= width:
        return text
    while text and draw.textlength(text + "…", font=f) > width:
        text = text[:-1]
    return text + "…"


def ranked(groups: list[dict]) -> list[dict]:
    """거래대금 순. 카드 순서와 요약 카드의 순위표가 같은 정렬을 쓴다."""
    return sorted(groups, key=lambda g: -sum(eok(s["amount"]) for s in g["top5"]))


def build(out_dir: Path, groups: list[dict], market: dict, now: datetime) -> list[Path]:
    groups = ranked(groups)
    day = out_dir / f"{now:%Y%m%d}"
    made = [summary_card(now, groups, market).save(day / "01_monthly.png")]
    for i, g in enumerate(groups, 1):
        safe = "".join(ch for ch in g["keyword"] if ch.isalnum())
        made.append(theme_card(now, g, i, len(groups))
                    .save(day / f"{i + 1:02d}_{safe}.png"))
    return made


def load(path: Path):
    """앞 단계가 실패해 0바이트로 남은 파일을 JSONDecodeError 대신 빈 값으로 본다.

    워크플로가 `python market_summary.py > market_summary.json` 으로 만들기
    때문에, 그 스크립트가 죽어도 파일 자체는 빈 채로 생긴다.
    """
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    return json.loads(text) if text else None


def main() -> int:
    here = Path(__file__).parent
    args = [a for a in sys.argv[1:] if a != "--supply"]
    out_dir = Path(args[0]) if args else here / "cards"
    market = load(here / "market_summary.json")
    if "--supply" in sys.argv:
        from supply_top import load_supply
        supply = load_supply(here)
        if not supply:
            raise SystemExit("[중단] supply_groups.json 이 비었습니다 — supply_top.py 를 먼저.")
        if not market:
            raise SystemExit("[중단] 지수 요약이 비어 있습니다 — market_summary.py 를 먼저.")
        now, _ = as_of()
        made = build_supply(out_dir, supply, market, now)
        for p in made:
            print(f"  {p}")
        print(f"수급 카드 {len(made)}장")
        return 0
    groups = load(here / "final_groups.json")
    no_theme = not groups and no_theme_day()
    if not groups and not no_theme:
        raise SystemExit("[중단] 그룹이 0개입니다. 카드를 만들지 않습니다.")
    if not market:
        raise SystemExit("[중단] 지수 요약이 비어 있습니다 — market_summary.py 가 "
                         "실패했는지 보세요. 요약 카드에 지수를 넣을 수 없습니다.")
    # 카드에 찍히는 날짜도, 카드가 들어가는 폴더 이름도 '오늘'이 아니라
    # 이 글이 다루는 거래일이어야 한다. 지난 날짜를 다시 채우는 실행에서
    # now 를 쓰면 9/11 자료가 9/13 이라는 제목을 달고 나간다 -- 실제로 그렇게
    # 나갔다. theme_write.as_of 가 그 날짜의 단일 출처다.
    now, overridden = as_of()
    if overridden:
        print(f"[주의] THEME_AS_OF={now:%Y-%m-%d} — 이 날짜로 카드를 만듭니다.")
    if no_theme:
        print("주도 테마 없음 — '오늘의 테마 없음' 카드 한 장을 만듭니다")
        made = build_no_theme(out_dir, market, now)
    else:
        made = build(out_dir, groups, market, now)
    for p in made:
        print(f"  {p}")
    print(f"카드 {len(made)}장")
    return 0


if __name__ == "__main__":
    sys.exit(main())
