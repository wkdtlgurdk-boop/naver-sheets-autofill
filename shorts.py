#!/usr/bin/env python3
"""그날 카드로 유튜브 쇼츠용 세로 영상(1080x1920 mp4)을 만든다.

스레드에는 카드를 캐러셀로 올리고, 같은 카드를 영상으로 이어 붙여 쇼츠로도
올린다. 숫자를 새로 그리지 않고 cards.py 가 만든 PNG 를 그대로 쓴다 — 카드와
영상이 다른 말을 할 수 없게.

장면은 두 가지다.
  표지  : 요약 카드(01_monthly)의 머리(지수까지)가 먼저 뜨고, 테마 순위가
          1위부터 한 줄씩 왼쪽에서 들어온다. 다 뜬 뒤 잠깐 멈춘다.
  테마  : 테마 카드 한 장을 THEME_SECONDS(3초) 보여 주고 다음 장이 오른쪽에서
          밀고 들어온다.
길이는 고정하지 않는다. 테마가 8개면 약 28초, 4개면 약 16초다.

프레임은 Pillow 로 합성해 ffmpeg 에 날 RGB 로 흘려 넣는다(브라우저 없음).

사용: python shorts.py [카드폴더=cards] [출력=shorts/<날짜>.mp4]
     SHORTS_DEBUG_FRAMES="1.5,5" 를 주면 그 시각의 프레임을 PNG 로도 남긴다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

import cards
from as_of import as_of

W, H = 1080, 1920
FPS = 30
CARD_Y = 300                 # 카드(1080x1350)를 놓는 세로 위치. 위 300 은 제목 띠
BG = cards.BG
GOLD, INK, MUTED = cards.GOLD, cards.INK, cards.MUTED
TRACK = (58, 52, 48)         # 진행 막대 바탕

# 표지 카드에서 잘라 낼 경계. cards.summary_card 의 좌표에서 나온다 —
# 지수 아래 구분선이 555, 1행 글자 기준선이 615(행 간격 72), 푸터 구분선이 1236.
HEAD_END = 557
ROW_TOP = 615 - 48
ROW_STEP = 72
FOOT_TOP = 1200

# 시간(초)
T_HEAD = 0.45                # 표지 머리가 떠오르는 시간
T_ROW = 0.32                 # 다음 행이 들어오기까지 간격
T_ROW_IN = 0.38              # 한 행이 들어오는 데 걸리는 시간
HOLD = 1.0                   # 표지가 다 뜬 뒤 멈춤
THEME_SECONDS = 3.0          # 테마 카드 한 장 (사용자 지정)
T_SWIPE = 0.32               # 카드 넘김


@dataclass
class Timeline:
    n: int                   # 테마 수

    @property
    def cover(self) -> float:
        return T_HEAD + self.n * T_ROW + HOLD

    def start(self, k: int) -> float:
        """k=0 표지, k>=1 k번째 테마가 시작하는 시각."""
        return 0.0 if k == 0 else self.cover + (k - 1) * THEME_SECONDS

    @property
    def total(self) -> float:
        return self.cover + self.n * THEME_SECONDS

    def scene(self, t: float) -> int:
        k = 0
        for i in range(self.n + 1):
            if t >= self.start(i):
                k = i
        return k

    def frames(self) -> int:
        return round(self.total * FPS)


def ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def faded(im: Image.Image, a: float) -> Image.Image:
    """불투명도 a 의 RGBA 사본. a=1 이면 원본 그대로."""
    if a >= 1:
        return im
    out = im.copy()
    out.putalpha(out.getchannel("A").point(lambda v: int(v * a)))
    return out


class Shorts:
    def __init__(self, paths: list[Path], date_label: str, supply: bool = False):
        self.supply = supply     # '오늘의 수급' 영상 (supply-demand 워크플로, 매일)
        self.cover = Image.open(paths[0]).convert("RGBA")
        self.themes = [Image.open(p).convert("RGBA") for p in paths[1:]]
        self.tl = Timeline(len(self.themes))
        n = self.tl.n
        c = self.cover
        self.head = c.crop((0, 0, cards.W, HEAD_END))
        self.rows = [c.crop((0, ROW_TOP + i * ROW_STEP, cards.W, ROW_TOP + (i + 1) * ROW_STEP))
                     for i in range(n)]
        self.foot = c.crop((0, FOOT_TOP, cards.W, cards.H))
        self.base = self._frame_base(date_label, n)

    def _frame_base(self, date_label: str, n: int) -> Image.Image:
        """매 프레임 같은 부분: 배경과 위쪽 제목."""
        im = Image.new("RGBA", (W, H), BG + (255,))
        d = ImageDraw.Draw(im)
        d.text((cards.M, 118), f"{date_label} · 장 마감", font=cards.font("medium", 34),
               fill=MUTED, anchor="lm")
        f = cards.font("black", 76)
        head = "오늘의 수급 " if self.supply else "오늘의 테마 "
        d.text((cards.M, 232), head, font=f, fill=INK, anchor="ls")
        d.text((cards.M + d.textlength(head, font=f), 232), f"TOP {n}", font=f,
               fill=GOLD, anchor="ls")
        return im

    def _bottom(self, d: ImageDraw.ImageDraw, t: float, k: int):
        n, tl = self.tl.n, self.tl
        x0, x1, gap, y = cards.M, W - cards.M, 10, 1700
        seg = (x1 - x0 - gap * n) / (n + 1)
        for i in range(n + 1):
            a = x0 + i * (seg + gap)
            d.rounded_rectangle([a, y, a + seg, y + 8], radius=4, fill=TRACK)
            dur = tl.cover if i == 0 else THEME_SECONDS
            frac = 1.0 if i < k else 0.0 if i > k else min((t - tl.start(i)) / dur, 1.0)
            if frac > 0:
                d.rounded_rectangle([a, y, a + max(seg * frac, 8), y + 8], radius=4, fill=GOLD)
        f = cards.font("medium", 34)
        foot = "순매수 금액순" if self.supply else "거래대금 순 · 평균상승률"
        d.text((x0, 1770), foot, font=f, fill=MUTED, anchor="lm")
        d.text((x1, 1770), "전체 순위" if k == 0 else f"{k} / {n}",
               font=cards.font("bold", 34), fill=INK, anchor="rm")

    def frame(self, t: float) -> Image.Image:
        tl = self.tl
        k = tl.scene(t)
        im = self.base.copy()
        stage = Image.new("RGBA", (cards.W, cards.H), (0, 0, 0, 0))

        # 표지 — 첫 테마가 들어오는 동안 왼쪽으로 빠지며 흐려진다
        out = ease((t - tl.start(1)) / T_SWIPE) if k >= 1 and tl.n else 0.0
        if k == 0 or (k == 1 and out < 1):
            cx, co = int(-out * cards.W * 0.35), 1 - out
            hp = ease(t / T_HEAD)
            stage.alpha_composite(faded(self.head, co * hp), (cx, int((1 - hp) * 24)))
            stage.alpha_composite(faded(self.foot, co * hp), (cx, FOOT_TOP))
            for i, row in enumerate(self.rows):
                p = ease((t - T_HEAD - i * T_ROW) / T_ROW_IN)
                if p > 0:
                    stage.alpha_composite(faded(row, co * p),
                                          (cx + int((1 - p) * -60), ROW_TOP + i * ROW_STEP))

        # 테마 카드 — 앞 장은 왼쪽으로 빠지고, 현재 장이 오른쪽에서 들어온다
        if k >= 2:
            p = ease((t - tl.start(k)) / T_SWIPE)
            if p < 1:
                prev = self.themes[k - 2]
                stage.alpha_composite(faded(prev, 1 - p), (int(-p * cards.W * 0.35), 0))
        if k >= 1:
            p = ease((t - tl.start(k)) / T_SWIPE)
            card = self.themes[k - 1]
            x = int((1 - p) * cards.W)
            if x < cards.W:
                stage.alpha_composite(card.crop((0, 0, cards.W - x, cards.H)), (x, 0))

        im.alpha_composite(stage, (0, CARD_Y))
        self._bottom(ImageDraw.Draw(im), t, k)
        return im.convert("RGB")


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise SystemExit("ffmpeg 가 없습니다 — 워크플로에서 apt-get install ffmpeg")
    return path


def render(paths: list[Path], date_label: str, out: Path, debug_at: list[float] = (),
           supply: bool = False) -> float:
    s = Shorts(paths, date_label, supply)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 유튜브가 권하는 형태: H.264 + yuv420p + faststart. 소리 없는 영상이라 음성 트랙은 없다.
    cmd = [ffmpeg_bin(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    want = {round(x * FPS): x for x in debug_at}
    for i in range(s.tl.frames()):
        fr = s.frame(i / FPS)
        if i in want:
            fr.save(out.with_name(f"{out.stem}_{want[i]:.1f}s.png"))
        proc.stdin.write(fr.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg 인코딩 실패")
    return s.tl.total


def video_name(day: str, supply: bool = False) -> str:
    """테마 영상은 <날짜>.mp4, 수급 영상은 <날짜>_supply.mp4 (youtube_upload 가 같은 이름을 찾는다)."""
    return f"{day}_supply.mp4" if supply else f"{day}.mp4"


def main() -> int:
    here = Path(__file__).parent
    supply = "--supply" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--supply"]
    card_root = Path(args[0]) if args else here / "cards"
    now, _ = as_of()
    day = f"{now:%Y%m%d}"
    folder = card_root / day / cards.SUPPLY_DIR if supply else card_root / day
    paths = sorted(folder.glob("*.png"))
    if len(paths) < 2:
        print(f"[shorts] {folder} 에 카드가 모자랍니다 ({len(paths)}장) — 영상을 만들지 않습니다.")
        return 0
    out = Path(args[1]) if len(args) > 1 else here / "shorts" / video_name(day, supply)
    debug = [float(x) for x in os.environ.get("SHORTS_DEBUG_FRAMES", "").split(",") if x.strip()]
    total = render(paths, cards.header_date(now), out, debug, supply)
    print(f"[shorts] {out} ({len(paths) - 1}장, {total:.1f}초)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
