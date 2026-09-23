#!/usr/bin/env python3
"""오늘 블록을 Daily 시트에서 읽어 final_groups.json 으로 만든다.

    python groups_from_sheet.py            # final_groups.json 기록
    python groups_from_sheet.py --dry-run  # 읽고 출력만

## 왜 이게 필요한가

카드는 theme_scan.py 가 만든 final_groups.json 을 읽는다. 그런데 네이버가
2026-09-10 저녁 사이 금융 사이트를 Next.js 로 다시 만들면서 finance.naver.com
/sise/* 가 서버 렌더 표를 더 이상 주지 않는다(어느 경로로 들어가도 같은 120KB
React 껍데기). 그래서 스캔이 0개로 멈추고, 그 뒤 카드도 스레드도 통째로 못 간다.

하지만 **테마 분석 결과는 이미 시트에 있다.** 스캔을 되살리지 않아도 카드는
만들 수 있다 -- 시트가 종목명·상승률·거래대금을 그대로 들고 있고, 카드가
요구하는 것이 정확히 그 셋이기 때문이다(종목코드는 카드가 쓰지 않는다).

읽는 자리는 theme_write.write_daily 가 쓰는 자리와 같다.

    header_row      A열에 'M/D', 슬롯 열에 '종목명'
    header_row + 1  슬롯 첫 열에 키워드 (나머지 두 칸은 AVERAGEA/SUM 수식)
    header_row + 2~ 종목명 / 상승률 / 거래대금(억), 최대 5행

거래대금 단위에 주의. **변환하지 않는다.** theme_write.write_daily 가
s["amount"] 를 그대로 시트에 쓰므로 시트 칸도 final_groups.json 도 똑같이
백만원이다(cards.eok 가 100 으로 나눠 억을 만든다). 시트를 억으로 보고 100 을
곱했더니 9/11 카드에 596,940억(=59.7조)이 찍혀 공개 리포에 올라갔다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone

from as_of import as_of

KST = timezone(timedelta(hours=9))
DAILY_ID = "1xq_WNQh04xV1UsLm9ZRL6HhkQIJn-34G41zm87oR2h4"
SEARCH_ROWS = 700
CARD_ROWS = 5      # 카드가 그리는 줄 수
MAX_STOCKS = 15    # 블록이 이보다 길 수는 없다. 다음 블록으로 넘어가는 것을 막는 상한.
HEADER_MARK = "종목명"


def _cell(grid: list[list], row: int, col: int) -> str:
    """1-based 행, 0-based 열. 범위를 벗어나면 빈 문자열."""
    r = grid[row - 1] if 0 < row <= len(grid) else []
    return str(r[col]).strip() if col < len(r) else ""


def parse_rate(text: str) -> float | None:
    """'12.34%' -> 12.34. 퍼센트 서식이라 읽는 방식에 따라 0.1234 로도 온다.

    표시값(read_formatted)을 쓰면 언제나 '12.34%' 꼴이므로 그것을 기준으로 삼되,
    숫자만 온 경우도 받아 준다.
    """
    text = str(text).strip().replace(",", "")
    if not text:
        return None
    m = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*%$", text)
    if m:
        return float(m.group(1))
    try:
        return float(text)
    except ValueError:
        return None


def parse_amount_eok(text: str) -> int | None:
    """'1,689' -> 1689. 시트의 거래대금 열은 억 단위다."""
    text = str(text).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(round(float(text)))
    except ValueError:
        return None


def block_date(grid: list[list], header_row: int, look_up: int = 6) -> str:
    """이 블록의 날짜. 헤더행에서 위로 올라가며 처음 만나는 A열 값이다.

    날짜 셀은 블록 전체에 걸쳐 세로 병합돼 있는데, Sheets API 는 병합 범위의
    값을 **좌상단 한 칸에만** 준다(드라이브로 내보내면 모든 행에 반복돼 보여서
    모양이 다르다). 좌상단이 헤더행일 수도, 그 위일 수도 있으므로 한 칸만
    보면 안 되고 위로 훑어야 한다.
    """
    for row in range(header_row, max(0, header_row - look_up), -1):
        value = _cell(grid, row, 0)
        if value:
            return value
    return ""


def header_rows(grid: list[list]) -> list[int]:
    """'종목명' 헤더를 가진 행들 = 블록의 시작점들."""
    return [r for r in range(1, len(grid) + 1)
            if any(_cell(grid, r, c) == HEADER_MARK for c in range(1, 40))]


def find_header_row(grid: list[list], wanted: str) -> int | None:
    """날짜가 `wanted` 인 블록의 헤더행."""
    for row in header_rows(grid):
        if block_date(grid, row) == wanted:
            return row
    return None


def groups_from_grid(grid: list[list], wanted: str) -> list[dict]:
    """오늘 블록 -> cards.py 가 먹는 그룹 목록. 빈 슬롯은 건너뛴다."""
    header_row = find_header_row(grid, wanted)
    if header_row is None:
        return []

    slots = [c for c in range(1, 40) if _cell(grid, header_row, c) == HEADER_MARK]
    groups = []
    seen: dict[frozenset, str] = {}   # 이미 나온 종목 명단 -> 그 칸의 키워드
    for slot in slots:
        keyword = _cell(grid, header_row + 1, slot)
        if not keyword or keyword.startswith("#"):   # #DIV/0! 같은 빈 슬롯
            continue

        # 줄 수를 5로 고정하지 않는다. 9/11 우주/방산 블록에는 종목이 여섯 개
        # 들어 있었고(사람이 늘렸거나 예전 판이 썼거나), 다섯에서 자르면 카드
        # 합계가 시트 대표행과 1,935억 어긋났다. 끝까지 읽어 개수를 세고,
        # 카드에 그리는 것만 다섯으로 자른다.
        stocks = []
        for i in range(1, MAX_STOCKS + 1):
            row = header_row + 1 + i
            name = _cell(grid, row, slot)
            if not name:
                break      # 빈 줄이 블록의 끝이다
            rate = parse_rate(_cell(grid, row, slot + 1))
            amount = parse_amount_eok(_cell(grid, row, slot + 2))
            if rate is None or amount is None:
                continue
            # 단위 변환을 하지 않는다. theme_write.py 가 s["amount"] 를 그대로
            # 시트에 쓰므로(백만원), 시트 칸과 final_groups 의 단위가 이미 같다.
            # 억으로 보고 100 을 곱했더니 9/11 카드에 596,940억(=59.7조)이
            # 찍혔다 -- 실제는 5,969억이다.
            stocks.append({"name": name, "code": "", "rate": rate,
                           "amount": amount})

        if not stocks:
            continue

        # 같은 그룹이 여러 칸을 차지한 블록이 있다. theme_write 는 키워드
        # 문자열이 **정확히** 같을 때만 그 칸을 재사용하는데, 그룹 이름은
        # 모델이 그날그날 붙이는 자유 서술이라 하루에 여러 번 돌면 흔들린다.
        # 9/22 는 실행이 일곱 번 도는 동안 '반도체 기판·PCB(FC-BGA/SOCAMM)',
        # '반도체 기판·PCB', '반도체 기판·PCB (FC-BGA/SOCAMM)' 세 철자가
        # 각각 빈 칸을 하나씩 먹었다 -- 종목 다섯이 모두 같은 한 그룹인데
        # 카드도 스레드도 같은 테마를 세 장 냈다.
        #
        # 이름은 못 믿어도 명단은 믿을 수 있다. 종목이 똑같으면 같은 그룹으로
        # 보고 먼저 나온 칸(왼쪽)만 남긴다. 부분적으로 겹치는 칸은 건드리지
        # 않는다 -- 그쪽은 실제로 다른 테마일 수 있다.
        names = frozenset(s["name"] for s in stocks)
        if names in seen:
            print(f"  [{keyword}] {slots.index(slot) + 1}번째 칸은 "
                  f"'{seen[names]}' 와 종목이 같습니다 — 같은 그룹으로 보고 "
                  f"건너뜁니다.", file=sys.stderr)
            continue
        seen[names] = keyword

        groups.append({
            "keyword": keyword,
            # 시트는 병합 전 원본 테마명을 들고 있지 않다. 카드 하단 줄에는
            # 키워드를 그대로 쓴다 -- 없는 것을 지어내는 것보다 낫다.
            "source_themes": [keyword],
            # 카드가 "조건 충족 N종목 중 상위 M개" 로 쓰는 자리다. N 은 블록에
            # 실제로 든 수여야 한다 -- 자른 뒤의 수를 넣으면 언제나 "5 중 5" 다.
            "total_qualifying": len(stocks),
            "top5": stocks[:CARD_ROWS],
            # 블록에 실제로 들어 있던 전부. 카드는 top5 만 그리지만, 시트를
            # 그대로 내보내는 feed_export.py 는 자르지 않은 쪽이 필요하다 --
            # 사람이 여섯째 줄을 적어 넣은 블록에서 그 종목만 조용히 빠지면,
            # 받는 쪽에서는 시트에 없었던 것과 구별되지 않는다.
            "stocks": stocks,
        })
    return groups


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 출력만")
    ap.add_argument("--date", help="M/D 를 직접 지정 (기본: 오늘)")
    args = ap.parse_args()

    from sheets import Spreadsheet
    from theme_write import resolve_daily_gid

    # 지난 날짜를 다시 만드는 실행이면 그 날 블록을 읽어야 한다. 오늘을 읽으면
    # 9/11 을 되살리려던 실행이 아직 비어 있는 오늘 블록을 보고 멈춘다.
    now, overridden = as_of()
    wanted = args.date or f"{now.month}/{now.day}"
    if overridden and not args.date:
        print(f"THEME_AS_OF={now:%Y-%m-%d} — {wanted} 블록을 읽습니다")
    book = Spreadsheet(DAILY_ID)
    gid = resolve_daily_gid(book, now)
    grid = book.read_formatted(gid, f"A1:AN{SEARCH_ROWS}")

    groups = groups_from_grid(grid, wanted)
    if not groups:
        # 무엇을 받았는지 남긴다. 병합 셀은 API 가 좌상단 한 칸에만 값을 주므로
        # 드라이브로 볼 때와 모양이 다르다 -- 추측하지 말고 실물을 보고 맞춘다.
        marks = [r for r in range(1, len(grid) + 1)
                 if any(_cell(grid, r, c) == HEADER_MARK for c in range(1, 40))]
        print(f"  [진단] 격자 {len(grid)}행, '종목명' 헤더가 있는 행 {len(marks)}개: "
              f"{marks[-6:]}", file=sys.stderr)
        for r in marks[-3:]:
            a_near = [(rr, _cell(grid, rr, 0)) for rr in range(max(1, r - 2), r + 3)
                      if _cell(grid, rr, 0)]
            kw = [_cell(grid, r + 1, c) for c in range(1, 40)
                  if _cell(grid, r, c) == HEADER_MARK]
            print(f"  [진단] {r}행 주변 A열 {a_near} / 키워드 {kw}", file=sys.stderr)
        a_vals = sorted({_cell(grid, r, 0) for r in range(1, len(grid) + 1)
                         if _cell(grid, r, 0)})
        print(f"  [진단] A열에 있는 값 {len(a_vals)}종: {a_vals[-12:]}", file=sys.stderr)
        # 오늘 블록이 비어 있는 것은 '조용한 날'이 아니라 아직 안 채워진 것이다.
        # 카드를 빈 채로 만들어 올리는 것보다 여기서 멈추는 편이 낫다.
        print(f"[중단] Daily 시트에서 {wanted} 블록을 찾지 못했거나 비어 있습니다.",
              file=sys.stderr)
        return 1

    print(f"{wanted} 블록에서 {len(groups)}개 그룹:")
    for g in groups:
        head = g["top5"][0]
        print(f"  [{g['keyword']}] {len(g['top5'])}종목, "
              f"1위 {head['name']} {head['rate']:+.2f}% "
              f"거래대금 합계 {sum(s['amount'] for s in g['top5']) // 100:,}억")

    if args.dry_run:
        print("dry-run — final_groups.json 을 쓰지 않았습니다")
        return 0

    with open("final_groups.json", "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)
    print(f"final_groups.json 기록 ({len(groups)}개 그룹)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
