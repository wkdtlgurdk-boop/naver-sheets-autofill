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


def footer_text() -> str:
    """조건을 카드 밑에 적어 둔다. theme_scan 의 상수에서 만들어야 낡지 않는다."""
    return (f"시가총액 {ts.MIN_CAP:,}억↑·상승률 {ts.MIN_RATE:g}%↑ "
            f"({ts.SURGE_RATE:g}%↑는 {ts.SURGE_CAP:,}억↑) 종목 {ts.MIN_COUNT}개 이상인 테마")


def eok(amount_mn: float) -> int:
    """거래대금 백만원 -> 억원. final_groups.json 은 백만 단위로 담고 있다."""
    return round(amount_mn / 100)


def avg_rate(stocks: list[dict]) -> float:
    return sum(s["rate"] for s in stocks) / len(stocks) if stocks else 0.0


def spaced(keyword: str) -> str:
    """제목에서만 가운뎃점 양옆을 띄운다 — 원본 카드가 그렇게 쓴다."""
    return keyword.replace("·", " · ")


class Card:
    def __init__(self, now: datetime, badge: str):
        self.im = Image.new("RGB", (W, H), BG)
        self.d = ImageDraw.Draw(self.im)
        self._header(now, badge)
        self._footer()

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

    def _footer(self):
        self.d.rectangle([M, 1236, R, 1237], fill=RULE_S)
        self.d.text((M, 1275), footer_text(), font=font("regular", SZ["footer"]),
                    fill=DIM, anchor="lm")

    def title(self, text: str, sub: str):
        self.d.text((M, 258), text, font=font("black", SZ["title"]), fill=INK, anchor="ls")
        self.d.text((M, 329), sub, font=font("medium", SZ["sub"]), fill=MUTED, anchor="ls")

    def row_rule(self, y: int, thick: int = 1):
        self.d.rectangle([M, y, R, y + thick - 1], fill=RULE_R if thick == 1 else RULE_S)

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.im.save(path, "PNG", optimize=True)
        return path


def theme_card(now: datetime, group: dict, rank: int, total: int) -> Card:
    """테마 한 장. rank 는 거래대금 순위(배지의 '1 / 7')."""
    c = Card(now, f"{rank} / {total}")
    top = group["top5"]
    c.title(spaced(group["keyword"]),
            f"조건 충족 {group['total_qualifying']}종목 중 상위 {len(top)}개")

    # 요약 두 칸: 평균상승률 / 거래대금
    c.d.text((M, 398), "평균상승률", font=font("medium", SZ["stat_label"]), fill=MUTED, anchor="ls")
    c.d.text((M, 455), f"{avg_rate(top):+.2f}%", font=font("black", SZ["stat_value"]),
             fill=RED, anchor="ls")
    c.d.text((378, 398), "거래대금", font=font("medium", SZ["stat_label"]), fill=MUTED, anchor="ls")
    c.d.text((378, 455), f"{sum(eok(s['amount']) for s in top):,}억",
             font=font("black", SZ["stat_value"]), fill=INK, anchor="ls")
    c.d.rectangle([M, 515, R, 516], fill=RULE_S)

    # 종목 5행. 1위만 금색 — 원본이 그렇다.
    y = 586
    for i, s in enumerate(top):
        c.d.text((M, y), s["name"], font=font("bold", SZ["row_name"]),
                 fill=GOLD if i == 0 else INK, anchor="ls")
        c.d.text((813, y), f"{s['rate']:+.2f}%", font=font("black", SZ["row_rate"]),
                 fill=RED, anchor="rs")
        c.d.text((1008, y), f"{eok(s['amount']):,}억", font=font("medium", SZ["row_amt"]),
                 fill=MUTED, anchor="rs")
        if i < len(top) - 1:
            c.row_rule(y + 32)
        y += 92

    # 묶은 테마 — 이 그룹이 네이버의 어느 테마들을 합친 것인지 밝힌다.
    c.d.rounded_rectangle([M, 1057, R, 1160], radius=14, fill=PANEL)
    c.d.text((M + 25, 1098), "묶은 테마", font=font("medium", SZ["box_label"]),
             fill=MUTED, anchor="ls")
    c.d.text((M + 25, 1135), fit(c.d, " · ".join(group["source_themes"]),
                                 font("medium", SZ["box_body"]), R - M - 50),
             font=font("medium", SZ["box_body"]), fill=SOFT, anchor="ls")
    return c


def summary_card(now: datetime, groups: list[dict], market: dict) -> Card:
    c = Card(now, "MONTHLY")
    c.title("오늘의 테마", f"조건 충족 테마 {len(groups)}개 · 거래대금 순")

    for x, key, label in ((M, "KOSPI", "KOSPI"), (564, "KOSDAQ", "KOSDAQ")):
        m = market[key]
        c.d.text((x, 400), label, font=font("bold", SZ["idx_label"]), fill=MUTED, anchor="ls")
        c.d.text((x, 480), f"{round(m['index']):,}", font=font("black", SZ["idx_value"]),
                 fill=INK, anchor="ls")
        f = font("medium", SZ["idx_sub"])
        head = f"거래대금 {m['amount_trillion']}조 · 외인 "
        c.d.text((x, 518), head, font=f, fill=MUTED, anchor="ls")
        won = m["foreign_eok"]
        c.d.text((x + c.d.textlength(head, font=f), 518), f"{won:+,}억",
                 font=font("bold", SZ["idx_sub"]), fill=RED if won >= 0 else (90, 150, 255),
                 anchor="ls")
    c.d.rectangle([517, 378, 518, 522], fill=RULE_S)
    c.d.rectangle([M, 555, R, 556], fill=RULE_S)

    y = 615
    for i, g in enumerate(groups):
        c.d.text((M, y), str(i + 1), font=font("medium", SZ["m_rank"]),
                 fill=GOLD if i == 0 else MUTED, anchor="ls")
        c.d.text((148, y), g["keyword"], font=font("bold", SZ["m_name"]),
                 fill=GOLD if i == 0 else INK, anchor="ls")
        c.d.text((814, y), f"{avg_rate(g['top5']):+.2f}%", font=font("black", SZ["m_rate"]),
                 fill=RED, anchor="rs")
        c.d.text((1008, y), f"{sum(eok(s['amount']) for s in g['top5']):,}억",
                 font=font("medium", SZ["m_amt"]), fill=MUTED, anchor="rs")
        if i < len(groups) - 1:
            c.row_rule(y + 23)
        y += 72
    return c


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


def main() -> int:
    here = Path(__file__).parent
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "cards"
    groups = json.loads((here / "final_groups.json").read_text(encoding="utf-8"))
    market = json.loads((here / "market_summary.json").read_text(encoding="utf-8"))
    if not groups:
        raise SystemExit("[중단] 그룹이 0개입니다. 카드를 만들지 않습니다.")
    made = build(out_dir, groups, market, datetime.now(KST))
    for p in made:
        print(f"  {p}")
    print(f"카드 {len(made)}장")
    return 0


if __name__ == "__main__":
    sys.exit(main())
