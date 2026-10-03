#!/usr/bin/env python3
"""테마 스캔 결과를 Daily / Monthly Keyword Calendar 에 기록한다.

theme_scan.py 가 만든 final_groups.json 과 market_summary.py 의 지수 요약을
읽어 두 시트에 쓴다. 예전에는 이 단계를 LLM 이 크롬 화면을 보며 클립보드로
붙여넣었는데, 블록 위치 탐색이 시각 판단이라 불안정했다. Sheets API 로는
A열과 헤더행을 그대로 읽어서 찾으므로 탐색이 결정적이다.

안전 원칙: 위치를 확신할 수 없으면 아무것도 쓰지 않고 죽는다. 예전 프롬프트의
"블록을 못 찾으면 시트를 건드리지 말고 멈춰라" 규칙을 그대로 따른다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sheets import Spreadsheet

from as_of import as_of as _as_of

KST = timezone(timedelta(hours=9))

DAILY_ID = "1xq_WNQh04xV1UsLm9ZRL6HhkQIJn-34G41zm87oR2h4"
DAILY_TEMPLATE_GID = 253402445  # [복사해서 쓰시오 지우지마시오]
MONTHLY_ID = "1OtZb8hDwb_vK1yGo6StT09jEUytXD9uoxB6Vxmq69ss"

STOCK_ROWS = 5     # 슬롯당 기록하는 종목 수
SEARCH_ROWS = 700  # 달력 탭에서 훑어볼 행 범위

# 탭은 gid 가 아니라 제목으로 찾는다. gid 를 상수로 박아두면 달이 바뀌어도
# 그대로 남아 지난달 탭에 조용히 쓴다 — 실제로 gid 가 9월 내내 8월
# 탭(26.8월)을 가리키고 있었다. 제목은 오늘 날짜에서 만들어지므로 낡지 않는다.
MONTHLY_LABEL = "거래대금(조)"  # 월간 블록의 둘째 행. 블록 경계를 세는 기준.

# 월간 시트: 요일별 3열 묶음.
MONTHLY_COLS = {
    0: ("B", "C", "D"), 1: ("E", "F", "G"), 2: ("H", "I", "J"),
    3: ("K", "L", "M"), 4: ("N", "O", "P"),
}


def col_letter(idx: int) -> str:
    """0-based 열 인덱스를 A1 표기로."""
    out = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def daily_tab_title(now: datetime) -> str:
    """오늘이 속한 Daily 탭 이름. 기존 탭들이 쓰는 '26.9월' 표기를 따른다."""
    return f"{now.year % 100}.{now.month}월"


def same_month(text: str, now: datetime) -> bool:
    """A열 날짜 글자가 이번 달인가. '10/1' 과 '2026-10-01' 두 표기를 읽는다.

    날짜로 읽히지 않는 글자는 이번 달이 아닌 것으로 본다.
    """
    text = text.strip()
    try:
        if "-" in text:
            return int(text.split("-")[1]) == now.month
        if "/" in text:
            return int(text.split("/")[0]) == now.month
    except (ValueError, IndexError):
        pass
    return False


def resolve_daily_gid(book: Spreadsheet, now: datetime) -> int:
    """이번 달 Daily 탭. 없으면 템플릿을 복사해 만든다.

    새 달 첫 영업일에 사람이 탭을 미리 만들어 두지 않으면 예전에는 그대로
    실패했다. 템플릿 탭은 블록 구조(병합 날짜셀·대표행 수식·백분율 서식)를
    그대로 들고 있으므로 복사가 곧 한 달치 준비다.
    """
    title = daily_tab_title(now)
    gid = book.gid_for_title(title)
    if gid is not None:
        return gid

    book.title_for_gid(DAILY_TEMPLATE_GID)  # 템플릿이 없으면 여기서 멈춘다
    gid = book.duplicate_tab(DAILY_TEMPLATE_GID, title)
    print(f"  Daily: '{title}' 탭이 없어 템플릿에서 새로 만들었습니다 (gid {gid})")
    return gid


def resolve_monthly_gid(book: Spreadsheet, now: datetime) -> int:
    """올해 Monthly 탭. 분기별 탭이 연 단위 한 탭으로 합쳐졌다."""
    title = str(now.year)
    gid = book.gid_for_title(title)
    if gid is None:
        raise SystemExit(
            f"[중단] Monthly 에 '{title}' 탭이 없습니다. 현재 탭: {book.titles()}. "
            "연간 탭은 병합·서식이 많아 자동 생성하지 않습니다 — 직접 만들어 주세요."
        )
    return gid


def ensure_daily_date(book: Spreadsheet, gid: int, now: datetime) -> int:
    """오늘 블록의 헤더행. A열 날짜가 비어 있으면 채우고 진행한다.

    한 달치 블록은 미리 만들어져 있지만 A열 날짜는 비어 있는 게 정상이라,
    "A열에서 오늘을 찾아라"만으로는 매일 멈춘다. 직전까지 날짜가 채워진
    블록들의 바로 다음 빈 블록이 오늘 블록이다. 날짜를 미리 순서대로 세어
    채우지 않는 이유는 공휴일 때문이다 — 하루 쉬면 그 뒤가 전부 어긋난다.

    A열 날짜는 이 스크립트만 쓰는 게 아니다. 스레드 게시 봇도 같은 열로
    블록을 찾으므로, 비워둔 채 종목만 채우면 그쪽이 조용히 못 찾는다.
    """
    wanted = f"{now.month}/{now.day}"
    grid = book.read_formatted(gid, f"A1:Z{SEARCH_ROWS}")

    def cell(r: int, c: int = 0) -> str:
        row = grid[r - 1] if r - 1 < len(grid) else []
        return str(row[c]).strip() if c < len(row) else ""

    headers = [r for r in range(1, len(grid) + 1)
               if any(str(v).strip() == "종목명" for v in (grid[r - 1] or []))]
    if not headers:
        raise SystemExit("[중단] '종목명' 헤더행이 하나도 없습니다. 탭 구조를 확인하세요.")

    # 중복 검사는 헤더행이 아니라 A열 전체를 본다. 같은 날짜가 두 번 적혀
    # 있으면 어느 블록인지 정할 수 없고, 같은 열로 블록을 찾는 스레드 게시
    # 봇도 똑같이 헷갈린다 — 조용히 한쪽을 고르면 안 된다.
    hits = [r for r in range(1, len(grid) + 1) if cell(r) == wanted]
    if len(hits) > 1:
        raise SystemExit(
            f"[중단] A열에 '{wanted}' 가 {len(hits)}곳 있습니다 (행 {hits}). "
            "어느 블록인지 자동으로 정할 수 없습니다."
        )
    if hits:
        if hits[0] not in headers:
            raise SystemExit(
                f"[중단] A{hits[0]}='{wanted}' 이지만 그 행에 '종목명' 헤더가 "
                "없습니다. 블록 구조가 예상과 다릅니다."
            )
        return hits[0]

    # 아직 날짜가 없다. 앞에서부터 채워진 구간을 지나 처음 만나는 빈 블록이
    # 오늘 자리다. 그 뒤에 엉뚱한 날짜가 박힌 블록이 있어도 여기서 멈추므로
    # 영향을 받지 않는다.
    #
    # '날짜가 적혀 있다'만으로 채워진 블록으로 치지 않는다. 템플릿 탭 A75 에
    # 예전에 쓰던 '2/11' 이 남아 있어서, 10월 탭을 복사해 만든 첫날(2026-10-01)
    # 앞 일곱 블록을 건너뛰고 A85 에 썼다. 이번 달 날짜이거나 대표행에 종목이
    # 들어 있는 블록만 채워진 것으로 본다 — 9월 탭의 8/31 처럼 달력이 지난달
    # 월요일부터 시작하는 블록은 종목이 있으므로 그대로 채워진 블록이다.
    target = None
    seen_dated = False
    for r in headers:
        if cell(r) and (same_month(cell(r), now) or cell(r + 1, 1)):
            seen_dated = True
            continue
        if cell(r):
            print(f"  Daily: A{r}='{cell(r)}' 은 이번 달 날짜가 아니고 블록이 "
                  "비어 있어 빈 블록으로 봅니다 (템플릿에 남은 날짜)")
        if seen_dated:
            target = r
            break
    if target is None and not seen_dated:
        # 채워진 블록이 하나도 없다 — 새 달 첫 거래일. 첫 블록이 오늘 자리다.
        target = headers[0]
    elif target is None:
        # 앞 블록이 전부 날짜로 찼다. 템플릿 블록은 17개인데 한 달 영업일은
        # 22~23일이라, 달력이 1일이 아니라 그 주 월요일(9월은 8/31)부터 시작하면
        # 넷째 주 중간에 바닥난다 — 2026-09-23 이 그렇게 멈췄다. 사람이 블록을
        # 복사해 붙이던 일을 여기서 한다.
        target = extend_daily(book, gid, headers, grid)
    if target is None:
        raise SystemExit(
            f"[중단] '{wanted}' 도 없고 채울 빈 블록도 없습니다. "
            "이번 달 탭의 블록이 모두 소진되었습니다."
        )

    # 빈 블록인지 확인한 뒤에만 날짜를 적는다. 대표행에 종목명이 이미 있으면
    # 남의 데이터이므로 건드리지 않는다.
    if cell(target + 1, 1):
        raise SystemExit(
            f"[중단] {target}행이 빈 블록이 아닙니다 "
            f"(대표행 B{target + 1}='{cell(target + 1, 1)}'). 시트를 건드리지 않습니다."
        )

    book.write(gid, f"A{target}", [[now.strftime("%Y-%m-%d")]])
    print(f"  Daily: A{target} 에 오늘 날짜({wanted}) 기입 — 빈 블록이었음")
    return target


def extend_daily(book: Spreadsheet, gid: int, headers: list[int],
                 grid: list[list]) -> int:
    """마지막 블록 아래에 템플릿의 빈 블록을 하나 붙이고 그 헤더행을 돌려준다.

    채워진 블록을 복사하지 않고 템플릿 탭의 마지막 블록을 가져온다. 그쪽은
    값이 비어 있고 대표행 수식(AVERAGEA/SUM)만 들고 있어서, 붙인 뒤 지울
    것이 없다. 블록 높이도 템플릿의 헤더 간격에서 읽는다 — 가정하지 않는다.
    """
    tpl = book.read_formatted(DAILY_TEMPLATE_GID, f"A1:Z{SEARCH_ROWS}")
    tpl_headers = [r for r in range(1, len(tpl) + 1)
                   if any(str(v).strip() == "종목명" for v in (tpl[r - 1] or []))]
    if len(tpl_headers) < 2:
        raise SystemExit("[중단] 템플릿 탭에서 블록을 두 개 이상 찾지 못해 "
                         "블록 높이를 알 수 없습니다. 탭을 늘리지 않습니다.")
    height = tpl_headers[-1] - tpl_headers[-2]
    src = tpl_headers[-1]
    new = headers[-1] + height

    # 붙일 자리가 비어 있어야 한다. 표 아래에 사람이 적어 둔 메모를 덮지 않는다.
    for r in range(new - 1, new - 1 + height):
        row = grid[r - 1] if r - 1 < len(grid) else []
        if any(str(v).strip() for v in row):
            raise SystemExit(f"[중단] 새 블록 자리({r}행)가 비어 있지 않습니다. "
                             "탭을 늘리지 않습니다.")

    book.copy_rows(DAILY_TEMPLATE_GID, src - 1, src - 2 + height, gid, new - 1)
    print(f"  Daily: 블록이 모두 차서 템플릿 블록을 {new - 1}~{new - 2 + height}행에 붙였습니다")
    return new


def as_of() -> tuple[datetime, bool]:
    """as_of.py 로 옮겼다. 카드·스레드·시트가 같은 날짜를 봐야 한다.

    부르는 자리를 그대로 두려고 이름만 남긴다.
    """
    return _as_of()


def clear_daily_slots(book: Spreadsheet, gid: int, header_row: int,
                      slots: list[int]) -> None:
    """그 날 블록의 키워드·종목 칸을 비운다. 대표행 수식은 건드리지 않는다.

    이미 채워진 슬롯은 평소에 절대 덮어쓰지 않는다(남의 데이터일 수 있다).
    다시 채우려면 그 방어를 명시적으로 풀어야 하므로, 지우는 범위를 쓰는
    범위와 정확히 같게 둔다: 키워드 칸과 종목 다섯 줄, 그리고 상승률·거래대금
    열의 종목 줄만. 대표행(header_row+1)의 둘째·셋째 칸에는 AVERAGEA/SUM 이
    들어 있고 그건 한 번 지우면 사람이 다시 넣어야 한다.
    """
    for slot in slots:
        c1, c2, c3 = (col_letter(slot), col_letter(slot + 1), col_letter(slot + 2))
        blank = [[""] for _ in range(STOCK_ROWS + 1)]
        book.write(gid, f"{c1}{header_row + 1}:{c1}{header_row + 1 + STOCK_ROWS}", blank)
        book.write(gid, f"{c2}{header_row + 2}:{c2}{header_row + 1 + STOCK_ROWS}",
                   blank[:STOCK_ROWS])
        book.write(gid, f"{c3}{header_row + 2}:{c3}{header_row + 1 + STOCK_ROWS}",
                   blank[:STOCK_ROWS])
    print(f"  Daily: 슬롯 {len(slots)}개를 비웠습니다 "
          f"(대표행 수식은 그대로)")


def write_daily(book: Spreadsheet, gid: int, groups: list[dict],
                now: datetime, clear_first: bool = False) -> list[dict]:
    """Daily Keyword Calendar 에 오늘 블록을 채우고, 대표행 계산값을 돌려준다."""
    wanted = f"{now.month}/{now.day}"
    header_row = ensure_daily_date(book, gid, now)

    # 헤더행에서 슬롯 위치를 읽는다. 슬롯 수는 주마다 다를 수 있어(기본 6개,
    # 필요하면 사용자가 오른쪽으로 늘려둠) 고정하지 않는다.
    header = book.read_formatted(gid, f"A{header_row}:Z{header_row}")
    header = header[0] if header else []
    slots = [i for i, v in enumerate(header) if str(v).strip() == "종목명"]
    if not slots:
        raise SystemExit(
            f"[중단] {header_row}행이 헤더행이 아닙니다 ('종목명' 없음). "
            f"A{header_row}='{wanted}' 이지만 블록 구조가 예상과 다릅니다."
        )
    print(f"  Daily: A{header_row}='{wanted}', 슬롯 {len(slots)}개 "
          f"({', '.join(col_letter(s) for s in slots)})")

    # 슬롯별 현재 상태를 먼저 본다. 하루 전체를 한 칸으로 판단하면 안 된다 —
    # 20:10 로컬 작업(과 이 스크립트)은 슬롯을 하나씩 쓰므로 도중에 죽으면
    # 하루가 반만 찬 채로 남는다. 첫 칸만 보고 "이미 기록됨"으로 건너뛰면
    # 나머지 슬롯이 영영 비어 있게 된다.
    if clear_first:
        clear_daily_slots(book, gid, header_row, slots)

    kw_row = book.read_formatted(gid, f"A{header_row + 1}:Z{header_row + 1}")
    kw_row = kw_row[0] if kw_row else []

    def keyword_at(slot: int) -> str:
        return str(kw_row[slot]).strip() if slot < len(kw_row) else ""

    taken = {keyword_at(s): s for s in slots if keyword_at(s)}
    free = [s for s in slots if not keyword_at(s)]
    if taken:
        print(f"  Daily: 이미 채워진 슬롯 {len(taken)}개 — "
              f"{', '.join(f'{k}@{col_letter(v)}' for k, v in taken.items())}")

    # 같은 키워드가 이미 자리를 잡고 있으면 그 자리를 쓰고, 아니면 빈 자리에
    # 넣는다. 남의 키워드가 든 슬롯은 절대 덮어쓰지 않는다.
    plan, dropped = [], []
    for g in groups:
        if g["keyword"] in taken:
            plan.append((g, taken[g["keyword"]]))
        elif free:
            plan.append((g, free.pop(0)))
        else:
            dropped.append(g["keyword"])
    if dropped:
        print(f"  Daily: 빈 슬롯이 없어 {len(dropped)}개 제외 — {', '.join(dropped)}")

    for g, slot in plan:
        c1, c2, c3 = col_letter(slot), col_letter(slot + 1), col_letter(slot + 2)

        # 이 슬롯이 이미 온전히 채워져 있으면 건너뛴다. '온전히'는 이 그룹이
        # 쓸 종목 수만큼 종목명/상승률/거래대금이 모두 있다는 뜻이다. 반만
        # 찬 슬롯은 다시 쓴다 — 같은 값을 덮는 것이므로 무해하다.
        want = len(g["top5"][:STOCK_ROWS])
        block = book.read(gid,
                          f"{c1}{header_row + 1}:{c3}{header_row + 1 + want}")

        def filled(row_idx: int) -> bool:
            row = block[row_idx] if row_idx < len(block) else []
            return len(row) >= 3 and all(str(v).strip() for v in row[:3])

        if keyword_at(slot) == g["keyword"] and all(filled(i) for i in range(1, want + 1)):
            print(f"    [{g['keyword']}] {c1}{header_row + 1} 이미 완전히 채워짐 — 건너뜀")
            continue

        # 대표행(header_row+1)의 상승률/거래대금 칸에는 AVERAGEA/SUM 수식이
        # 들어 있다. 종목명 칸만 쓰고 나머지 두 칸은 절대 건드리지 않는다.
        formulas = book.read_formulas(gid, f"{c2}{header_row + 1}:{c3}{header_row + 1}")
        cells = formulas[0] if formulas else []
        if not (len(cells) >= 2
                and str(cells[0]).startswith("=") and str(cells[1]).startswith("=")):
            raise SystemExit(
                f"[중단] {c2}{header_row + 1}:{c3}{header_row + 1} 에 수식이 없습니다 "
                f"(읽은 값: {cells}). 템플릿이 준비되지 않은 블록입니다."
            )

        top = g["top5"][:STOCK_ROWS]
        names = [g["keyword"]] + [s["name"] for s in top]
        # 상승률은 '12.34%' 문자열로 보낸다. 이 열은 백분율 서식이라 숫자
        # 12.34 를 넣으면 1234% 가 된다 — 클립보드 시절과 같은 표기를 유지한다.
        rates = [[f"{s['rate']:.2f}%"] for s in top]
        amounts = [[int(s["amount"])] for s in top]

        book.write(gid, f"{c1}{header_row + 1}:{c1}{header_row + len(names)}",
                   [[n] for n in names])
        book.write(gid, f"{c2}{header_row + 2}:{c2}{header_row + 1 + len(rates)}", rates)
        book.write(gid, f"{c3}{header_row + 2}:{c3}{header_row + 1 + len(amounts)}", amounts)
        print(f"    [{g['keyword']}] -> {c1}{header_row + 1} "
              f"({len(top)}종목, 1위 {top[0]['name']} {top[0]['rate']:+.2f}%)")

    return read_daily_totals(book, gid, header_row, slots)


def read_daily_totals(book: Spreadsheet, gid: int, header_row: int,
                      slots: list[int]) -> list[dict]:
    """대표행이 계산한 평균상승률/거래대금합을 시트에서 그대로 읽어온다.

    월간 시트에 이 값을 옮겨 적어야 하는데, 수식 의미를 파이썬에서 다시
    구현하면 언젠가 어긋난다. 시트가 계산한 값을 읽는 편이 항상 일치한다.
    """
    out = []
    for slot in slots:
        c1 = col_letter(slot)
        c3 = col_letter(slot + 2)
        row = book.read(gid, f"{c1}{header_row + 1}:{c3}{header_row + 1}")
        cells = row[0] if row else []
        if len(cells) < 3 or not str(cells[0]).strip():
            continue
        out.append({"keyword": cells[0], "rate": cells[1], "amount": cells[2]})
    return out


def as_percent(rate) -> str:
    """대표행에서 읽은 비율을 '12.75%' 표기로.

    Daily 의 상승률 열은 백분율 서식이라 write_daily 가 '12.75%' 문자열을
    넣는데, 그 칸을 UNFORMATTED_VALUE 로 되읽으면 0.1275 가 나온다. 그 숫자를
    Monthly 에 그대로 옮기면 서식이 없는 칸에서 '0.127525' 로 보인다 — 사람이
    채워둔 9/1 주가 '19.98%' 인 것과 어긋난다. 문자열로 보내면 서식이 있든
    없든 사람이 읽는 형태로 남는다.
    """
    if isinstance(rate, (int, float)):
        return f"{rate * 100:.2f}%"
    return str(rate)  # #DIV/0! 같은 오류 문자열은 그대로 옮긴다


def col_index(letter: str) -> int:
    """A1 열 문자를 0-based 인덱스로. col_letter 의 역함수."""
    out = 0
    for ch in letter:
        out = out * 26 + (ord(ch) - ord("A") + 1)
    return out - 1


def locate_monthly_block(book: Spreadsheet, gid: int, c1: str,
                         now: datetime) -> tuple[int, int]:
    """이번 달 섹션 안에서 오늘 블록의 시작행과 그룹행 용량을 찾는다.

    분기 탭이 연 단위 한 탭으로 합쳐지면서 날짜만으로는 못 찾는다 — 열두 달이
    한 탭에 있으니 같은 열에 같은 날짜가 여러 번 나온다(실측: B열의 '7' 이
    335·456·645 세 곳). 그래서 먼저 이번 달 섹션을 가르고 그 안에서만 찾는다.
    섹션 머리는 요일머리행(B='MON') 바로 위 행이고 거기에 월이 적혀 있다.

    용량도 고정이 아니다. 예전 코드는 6행으로 박아뒀는데 주마다 다르다
    (9/1 주는 6줄, 9/7 주는 8줄). 블록의 둘째 행이 항상 '거래대금(조)' 이므로
    다음 라벨행까지의 거리로 센다.
    """
    grid = book.read_formatted(gid, f"A1:P{SEARCH_ROWS}")

    def cell(r: int, c: int) -> str:
        row = grid[r - 1] if r - 1 < len(grid) else []
        return str(row[c]).strip() if c < len(row) else ""

    n = len(grid)
    heads = [r for r in range(2, n + 1) if cell(r, 1) == "MON"]
    if not heads:
        raise SystemExit("[중단] Monthly 에 요일머리행(B열 'MON')이 없습니다.")

    section = None
    for i, head in enumerate(heads):
        marker = cell(head - 1, 1)
        if marker.isdigit() and int(marker) == now.month:
            end = heads[i + 1] - 2 if i + 1 < len(heads) else n
            section = (head, end)
            break
    if section is None:
        months = sorted({cell(h - 1, 1) for h in heads})
        raise SystemExit(
            f"[중단] Monthly 에 {now.month}월 섹션이 없습니다 (있는 월: {months}). "
            "달력 블록을 먼저 만들어 주세요 — 병합·서식이 있어 자동 생성하지 않습니다."
        )
    start, end = section

    # 블록 시작행 = 바로 다음 행이 '거래대금(조)' 라벨인 행.
    labels = [r for r in range(start, end + 1) if cell(r, 1) == MONTHLY_LABEL]
    ci = col_index(c1)
    hits = [r for r in labels if cell(r - 1, ci) == str(now.day)]
    if not hits:
        raise SystemExit(
            f"[중단] {now.month}월 섹션({start}~{end}행) {c1}열에서 "
            f"'{now.day}' 를 찾지 못했습니다. 이번 주 블록이 아직 없습니다."
        )
    if len(hits) > 1:
        raise SystemExit(f"[중단] {c1}열에 '{now.day}' 가 여러 곳입니다: {hits}")

    label_row = hits[0]
    day_row = label_row - 1
    later = [r for r in labels if r > label_row]
    next_start = (later[0] - 1) if later else (end + 1)
    capacity = next_start - (day_row + 4)
    if capacity < 1:
        raise SystemExit(f"[중단] {day_row}행 블록의 그룹행 자리가 없습니다 ({capacity}).")
    return day_row, capacity


def write_monthly(book: Spreadsheet, gid: int, totals: list[dict], market: dict,
                  now: datetime) -> None:
    c1, c2, c3 = MONTHLY_COLS[now.weekday()]
    day_row, capacity = locate_monthly_block(book, gid, c1, now)
    print(f"  Monthly: {c1}{day_row}='{now.day}' ({c1}/{c2}/{c3} 열), "
          f"그룹행 {capacity}칸")

    kospi, kosdaq = market["KOSPI"], market["KOSDAQ"]
    book.write(gid, f"{c2}{day_row}:{c3}{day_row}",
               [[f"코스피 {round(kospi['index'])}", f"코스닥 {round(kosdaq['index'])}"]])
    book.write(gid, f"{c2}{day_row + 1}:{c3}{day_row + 1}",
               [[kospi["amount_trillion"], kosdaq["amount_trillion"]]])
    book.write(gid, f"{c2}{day_row + 2}:{c3}{day_row + 2}",
               [[kospi["foreign_eok"], kosdaq["foreign_eok"]]])
    print(f"    지수 {round(kospi['index'])}/{round(kosdaq['index'])}, "
          f"거래대금 {kospi['amount_trillion']}/{kosdaq['amount_trillion']}조, "
          f"외인 {kospi['foreign_eok']}/{kosdaq['foreign_eok']}억")

    # day_row+3 은 '평균상승률 / 거래대금합' 라벨 행이므로 건드리지 않는다.
    rows = [[t["keyword"], as_percent(t["rate"]), t["amount"]]
            for t in totals[:capacity]]
    if len(totals) > capacity:
        dropped = [str(t["keyword"]) for t in totals[capacity:]]
        print(f"    월간 블록은 {capacity}행까지라 제외 — {', '.join(dropped)}")
    start = day_row + 4
    book.write(gid, f"{c1}{start}:{c3}{start + len(rows) - 1}", rows)
    print(f"    키워드 {len(rows)}행 기록 ({c1}{start} 부터)")


def main() -> int:
    now, overridden = as_of()
    if overridden:
        print(f"[주의] THEME_AS_OF={now:%Y-%m-%d} — 오늘이 아니라 이 날짜의 "
              f"블록에 씁니다.")
    elif now.weekday() > 4:
        raise SystemExit(f"[중단] 주말({now:%A})이라 실행하지 않음.")
    clear_first = os.environ.get("THEME_CLEAR_FIRST") == "1"
    if clear_first and not overridden:
        raise SystemExit("[중단] THEME_CLEAR_FIRST 는 THEME_AS_OF 와 함께만 씁니다. "
                         "오늘 블록을 실수로 지우는 일을 막기 위한 것입니다.")

    here = Path(__file__).parent
    groups = json.loads((here / "final_groups.json").read_text(encoding="utf-8"))
    market = json.loads((here / "market_summary.json").read_text(encoding="utf-8"))
    if not groups:
        raise SystemExit("[중단] 조건을 만족하는 테마 그룹이 0개입니다. 시트를 건드리지 않습니다.")
    print(f"{now:%Y-%m-%d %H:%M} KST — 그룹 {len(groups)}개")

    daily = Spreadsheet(DAILY_ID)
    daily_gid = resolve_daily_gid(daily, now)
    print(f"  Daily 탭: '{daily.title_for_gid(daily_gid)}' (gid {daily_gid})")
    totals = write_daily(daily, daily_gid, groups, now, clear_first)

    monthly = Spreadsheet(MONTHLY_ID)
    monthly_gid = resolve_monthly_gid(monthly, now)
    print(f"  Monthly 탭: '{monthly.title_for_gid(monthly_gid)}' (gid {monthly_gid})")
    write_monthly(monthly, monthly_gid, totals, market, now)

    print("완료 — 두 시트 모두 기록됨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
