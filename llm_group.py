#!/usr/bin/env python3
"""오늘 스캔한 테마들을 Claude 에게 묶게 하고 keywords.json 을 갱신한다.

기계적 병합(merge_by_overlap)은 '오늘 조건을 충족한 종목'이 겹치는지만 본다.
그 집합은 날마다 흔들려서, 같은 산업의 두 테마가 서로 다른 종목이 올랐다는
이유로 갈라진다(9/7: 유리 기판과 플렉서블 디스플레이가 1위 종목이 같은데도
따로 남았다). 반대로 크게 뭉친 그룹은 '제일 짧은 이름'을 라벨로 달아서,
반도체 8개 테마가 합쳐진 그룹이 'LED' 라는 간판을 달고 나왔다.

두 문제 다 이름과 산업 지식이 필요한 판단이라 규칙으로는 한계가 있다.
그래서 이 단계만 모델에게 맡기고, 결과는 사람이 읽고 고칠 수 있는
keywords.json 으로 남긴다 — 다음 실행에 그대로 반영된다.

같은 이유로 두 가지를 더 맡긴다. 네이버 테마 목록은 지워지지 않아서 옛
이벤트 테마('일본 수출규제', '5G')가 남아 있고, 테마의 종목 구성도 낡는다
(통신 사업을 접은 회사가 통신 테마에, 수소 회사가 조선기자재에). 둘 다
이름과 사업 내용을 알아야 하는 판단이라 모델이 후보를 고르고, 결과는
exclusions.json 에 쌓인다 — 사람이 지우거나 더할 수 있고, 사람이 적은
항목을 이 단계가 덮어쓰지 않는다.

안전 원칙: 이 단계는 절대 파이프라인을 죽이지 않는다. 키가 없거나 호출이
실패하거나 응답이 검증을 통과하지 못하면, 기존 keywords.json 을 그대로 두고
정상 종료한다. 그러면 기계적 병합이 어제까지의 키워드로 돌아간다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import theme_scan as ts

KST = timezone(timedelta(hours=9))

MODEL = "claude-opus-5"
HERE = Path(__file__).parent
RAW = HERE / "raw_scan.json"
KEYWORDS = HERE / "keywords.json"
EXCLUSIONS = ts.EXCLUSION_FILE

# 시트의 하루 블록이 가진 슬롯 수. 그룹이 이보다 많으면 뒤쪽이 잘린다.
TARGET_GROUPS = 8

SYSTEM = """\
당신은 한국 주식 시장의 테마를 정리합니다.

네이버 증권의 테마는 서로 겹칩니다. 같은 산업을 다른 이름으로 부르거나
(예: '반도체 장비'와 'LED장비'), 한 종목이 여러 테마에 동시에 속합니다.
이걸 그대로 두면 일일 시트의 슬롯을 중복된 테마가 차지해서, 정작 다른
산업의 움직임이 밀려납니다.

오늘 조건을 충족한 테마 목록과 각 테마에서 오른 종목을 드립니다.
이것을 겹치지 않는 그룹으로 묶으세요.

규칙:
- 각 테마는 정확히 한 그룹에만 들어갑니다. 어느 그룹에도 안 넣어도 됩니다.
- 같은 산업·같은 공급망이거나 종목이 많이 겹치면 묶으세요.
- 그룹 이름(keyword)은 그 그룹을 가장 잘 대표하는 한국어 이름으로 직접
  지으세요. 묶인 테마 중 하나를 골라도 되고 새로 지어도 됩니다. 짧다는
  이유로 고르지 마세요 — 반도체 8개 테마를 묶은 그룹에 'LED'라는 이름이
  붙은 적이 있는데, 그룹의 내용을 오해하게 만듭니다.
- 그룹은 8개 이하가 되도록 묶으세요. 그보다 많으면 시트에서 잘립니다.
- 반도체 밸류체인은 한 흐름으로 봅니다. '반도체 장비'·'HBM'·'시스템반도체'·
  '온디바이스 AI'·후공정(OSAT·패키징)이 같은 날 함께 올랐으면 한 그룹으로
  묶으세요. 2026-09-29 에 반도체 하루가 장비·시스템반도체·기판·재료·유리기판
  다섯 칸으로 쪼개져, 칸마다 3~4종목짜리로 얇아지고 HPSP(장비)가 시스템반도체
  칸에 앉았습니다. 다만 '반도체 기판·PCB'와 '반도체 재료/부품'은 공급망이
  달라 따로 둡니다.
- 'OLED·디스플레이'와 '폴더블폰'은 사용자가 종목을 직접 고른 테마입니다. 다른
  테마와 묶지 말고 각각 한 그룹으로 두세요(유리기판·반도체 기판·폰 부품과도
  합치지 않습니다). 키워드는 'OLED·디스플레이' 그대로 씁니다.
- '2차전지 셀'·'2차전지 소재'·'2차전지 장비'도 사용자가 종목을 직접 고른
  테마입니다. 셋을 서로 합치지 말고, 다른 테마(리튬·ESS·반도체 재료 등)와도
  묶지 말고 각각 한 그룹으로 두세요.
- '로봇'도 사용자가 종목을 직접 고른 테마입니다. AI 소프트웨어·반도체·
  방산 테마와 합치지 말고 한 그룹으로 두세요. 키워드는 '로봇' 그대로 씁니다.
- 종목이 겹치지 않고 산업도 다르면 억지로 묶지 마세요. 서로 다른 테마를
  합치면 그룹의 평균상승률이 의미를 잃습니다.
- reason 은 왜 묶었는지 한 문장. 사람이 이 판단을 검토할 때 읽습니다.

묶는 것과 별개로, 두 가지를 더 골라 주세요. 둘 다 비워도 됩니다.

stale_themes — 지금의 시장 테마가 아니라 지난 사건의 잔재인 테마.
네이버의 테마 목록은 한 번 만들어지면 지워지지 않아서 몇 해 전 이벤트가
그대로 남아 있습니다. 예: '일본 수출규제'(2019년 한일 무역분쟁),
'5G'(2019~2020년 상용화). 이런 테마는 안의 종목이 오늘 각자의 이유로
올랐을 뿐인데 옛 이름이 하루의 슬롯을 차지합니다. 반대로 이름이 오래됐어도
지금 그 산업이 실제로 도는 테마는 (예: '반도체 장비') 고르지 마세요.
확신이 없으면 고르지 마세요 — 한 번 넣으면 다음 실행부터 계속 빠집니다.

misfit_stocks — 그 테마 안에 있지만 지금 사업 내용이 테마와 안 맞는 종목.
테마의 종목 구성도 같이 낡습니다. 예: 통신 사업을 접은 회사가 통신 테마에
남아 있거나, 수소 회사가 조선기자재에 들어 있는 경우. 지목한 테마에서만
빠지고 다른 테마에서는 그대로 남으므로, 그 종목이 진짜로 속한 테마를
같이 적어 주세요(belongs). 오늘 많이 올랐다는 이유로 고르지 마세요 —
사업 내용이 안 맞을 때만입니다."""

# CLI 에는 API 의 json_schema 출력 강제가 없다. 형식을 말로 못 박는다.
SCHEMA_NOTE = """\
답은 **JSON 만** 내보내세요. 설명·머리말·```울타리 없이 이 형태 그대로입니다.

{"groups": [{"keyword": "묶음 이름",
             "source_themes": ["오늘 목록에 있던 테마명", "..."],
             "reason": "왜 묶었는지 한 줄"}],
 "stale_themes": [{"theme": "테마명", "reason": "왜 낡았는지"}],
 "misfit_stocks": [{"stock": "종목명", "theme": "그 종목이 들어 있는 테마명",
                    "belongs": "원래 어울리는 자리", "reason": "왜 안 맞는지"}]}

source_themes 와 theme 에는 **드린 목록에 있는 테마명만** 정확히 그대로
적으세요. 없는 이름을 지어내면 그 항목은 버려집니다.
stale_themes 와 misfit_stocks 는 없으면 빈 배열로 두세요.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "source_themes": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["keyword", "source_themes", "reason"],
                "additionalProperties": False,
            },
        },
        "stale_themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "theme": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["theme", "reason"],
                "additionalProperties": False,
            },
        },
        "misfit_stocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stock": {"type": "string"},
                    "theme": {"type": "string"},
                    "belongs": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["stock", "theme", "belongs", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["groups", "stale_themes", "misfit_stocks"],
    "additionalProperties": False,
}

# 하루에 새로 제외할 수 있는 테마 수의 상한. 제외는 다음 실행부터 계속
# 유효하므로, 모델이 한 번 크게 헛짚으면 그날 하루가 아니라 그 뒤가 전부
# 얇아진다. 오늘 테마의 3분의 1을 넘게 지우자고 하면 판단이 아니라 오작동에
# 가까우므로 통째로 버린다.
STALE_SHARE_CAP = 3


FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")
TIMEOUT_SEC = 300


def cli_available() -> str | None:
    """구독으로 부를 수 있으면 None, 아니면 사유.

    API 키가 아니라 Claude Code CLI 로 부른다. 사용자가 구독자라 토큰당 과금
    대신 구독 한도 안에서 쓴다 -- industry-report-telegram-bot/summarizer.py 가
    같은 이유로 먼저 옮겨 갔고, 여기도 그 길을 따른다.

    CLI 는 CLAUDE_CODE_OAUTH_TOKEN 을 읽는다 (`claude setup-token` 으로 만드는
    1년짜리 토큰).
    """
    if shutil.which("claude") is None:
        return "claude CLI 가 없습니다"
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "CLAUDE_CODE_OAUTH_TOKEN 이 없습니다"
    if os.environ.get("ANTHROPIC_API_KEY"):
        # 인증 우선순위상 API 키가 이긴다. 구독으로 돌리려던 호출이 조용히
        # 과금되는 상황이라 로그로 남긴다.
        print("[llm_group] 경고: ANTHROPIC_API_KEY 가 설정돼 있어 구독 토큰 대신 "
              "API 키로 과금됩니다.", file=sys.stderr)
    return None


def explain(stderr: str, stdout: str) -> str:
    """CLI 가 실패했을 때 사람이 읽을 한 줄.

    CLI 는 --output-format json 이면 실패해도 JSON 봉투를 stdout 으로 뱉는다.
    그 봉투 앞쪽은 duration_api_ms, session_id, usage 처럼 고장 원인과 무관한
    필드라, 통째로 잘라 찍으면 정작 사유가 담긴 result/subtype 이 뒤에 밀려
    안 보인다. 2026-09-18~19 에 이것 때문에 "종료코드 1, 토큰 0" 만 사흘을
    들여다봤다 -- 무엇이 거부됐는지는 끝내 못 봤다.

    그래서 봉투를 열어 사유 필드부터 찍는다. JSON 이 아니면 있는 그대로.
    """
    raw = (stderr or "").strip() or (stdout or "").strip()
    if not raw:
        return "(출력 없음)"
    try:
        doc = json.loads(raw)
    except Exception:
        return raw[:400]
    bits = []
    for key in ("subtype", "result", "error", "message"):
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            bits.append(f"{key}={value.strip()[:240]}")
    if doc.get("is_error") is not None:
        bits.insert(0, f"is_error={doc['is_error']}")
    return " / ".join(bits) if bits else raw[:400]


def run_claude(prompt: str) -> str | None:
    """CLI 한 번. 본문 텍스트를 돌려주고, 실패하면 None.

    CLI 에는 API 의 json_schema 출력 강제가 없다. 대신 시스템 프롬프트로
    형식을 못 박고 받은 뒤 파싱한다 -- validate() 가 어차피 테마 이름까지
    대조하므로, 형식이 어긋나면 거기서 걸린다.
    """
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", MODEL,
        "--system-prompt", SYSTEM + "\n\n" + SCHEMA_NOTE,
        "--max-turns", "1",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--restricted",
        "--disallowedTools", "Bash", "Write", "Edit", "WebFetch", "WebSearch",
    ]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        print(f"[llm_group] {TIMEOUT_SEC}초 안에 끝나지 않았습니다.", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"[llm_group] claude 종료코드 {proc.returncode}: "
              f"{explain(proc.stderr, proc.stdout)}", file=sys.stderr)
        return None
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"[llm_group] CLI 출력을 읽지 못했습니다: {proc.stdout[:200]}",
              file=sys.stderr)
        return None
    if envelope.get("is_error") or envelope.get("subtype") != "success":
        print(f"[llm_group] 호출 실패: subtype={envelope.get('subtype')}", file=sys.stderr)
        return None
    # 사용량은 봉투에 들어 있다. 여기서 찍는다 -- 이 값이 손에 있는 자리가
    # 여기뿐이고, 호출부로 들고 나가면 또 없는 변수를 참조하게 된다.
    # get 으로 읽는 이유: 없는 필드 때문에 죽을 값이 아니다. 관측용일 뿐이다.
    usage = envelope.get("usage") or {}
    print(f"[llm_group] usage: in={usage.get('input_tokens', '?')} "
          f"out={usage.get('output_tokens', '?')}", file=sys.stderr)
    return envelope.get("result")


def strip_fence(text: str) -> str:
    """```json 울타리를 벗긴다. CLI 는 감싸서 줄 때가 있다."""
    return FENCE_RE.sub("", (text or "").strip())


def give_up(why: str) -> int:
    """기존 keywords.json 을 그대로 두고 정상 종료."""
    print(f"[llm_group] 건너뜀 — {why}", file=sys.stderr)
    print("[llm_group] 기존 keywords.json 을 그대로 사용합니다.", file=sys.stderr)
    return 0


def describe(raw: list[dict]) -> str:
    """테마 한 줄씩. 조건 통과 종목과 함께 테마 전체가 얼마나 움직였는지 붙인다.

    통과 종목만 보여주면 어느 테마든 오른 종목만 늘어놓은 것으로 보인다.
    구성 40종목 중 3개만 오른 테마와 8종목 중 6개가 오른 테마는 화면에서
    같아 보이지만 전자는 테마가 움직인 게 아니다 — stale_themes 판단에
    필요한 정보라 같이 준다.
    """
    lines = []
    for t in raw:
        stocks = ", ".join(
            f"{s['name']}({s['rate']:+.1f}%)" for s in t["qualifying"]
        )
        members = t.get("member_count")
        if members:
            head = (f"{t['theme_name']} [구성 {members}종목 중 "
                    f"{t.get('up_count', 0)}개 상승")
            if t.get("avg_rate") is not None:
                head += f", 평균 {t['avg_rate']:+.1f}%"
            head += "]"
        else:
            head = t["theme_name"]
        lines.append(f"- {head}: {stocks}")
    return "\n".join(lines)


def validate(groups: list[dict], known: set[str]) -> list[dict]:
    """스캔에 없는 테마명과 중복 배정을 걸러낸다.

    모델이 테마 이름을 살짝 바꿔 적으면 apply_keyword_overrides 가 아무것도
    못 찾고 조용히 무시한다 — 실패가 눈에 안 보인다. 여기서 걸러 로그에 남긴다.
    """
    seen: dict[str, str] = {}
    out = []
    for g in groups:
        kw = (g.get("keyword") or "").strip()
        if not kw:
            continue
        themes = []
        for name in g.get("source_themes", []):
            name = name.strip()
            if name not in known:
                print(f"[llm_group] 무시: '{name}' 는 오늘 스캔에 없는 테마입니다.",
                      file=sys.stderr)
                continue
            if name in seen:
                print(f"[llm_group] 무시: '{name}' 가 '{seen[name]}' 와 "
                      f"'{kw}' 양쪽에 있습니다.", file=sys.stderr)
                continue
            seen[name] = kw
            themes.append(name)
        if themes:
            out.append({"keyword": kw, "source_themes": themes,
                        "reason": (g.get("reason") or "").strip()})
    return out


def validate_exclusions(stale: list[dict], misfits: list[dict],
                       raw: list[dict]) -> tuple[list[dict], list[dict]]:
    """제외 제안에서 오늘 스캔에 실제로 있는 것만 남긴다.

    validate() 와 같은 이유다 — 모델이 이름을 살짝 바꿔 적으면 제외가
    아무것도 안 걸리거나(무해), 반대로 엉뚱한 이름이 패턴이 되어 다른
    테마를 부분 문자열로 잡아먹는다(유해). 후자를 막는다.
    """
    by_theme = {t["theme_name"]: {s["name"] for s in t["qualifying"]} for t in raw}

    ok_stale = []
    for item in stale:
        name = str(item.get("theme") or "").strip()
        if name not in by_theme:
            print(f"[llm_group] 무시: 제외 후보 '{name}' 는 오늘 스캔에 없는 테마입니다.",
                  file=sys.stderr)
            continue
        ok_stale.append({"theme": name, "reason": str(item.get("reason") or "").strip()})

    if len(ok_stale) > max(1, len(raw) // STALE_SHARE_CAP):
        print(f"[llm_group] 제외 후보가 {len(ok_stale)}개 — 오늘 테마 {len(raw)}개의 "
              f"1/{STALE_SHARE_CAP} 을 넘어 전부 무시합니다.", file=sys.stderr)
        ok_stale = []

    ok_misfits = []
    for item in misfits:
        theme = str(item.get("theme") or "").strip()
        stock = str(item.get("stock") or "").strip()
        if theme not in by_theme:
            print(f"[llm_group] 무시: '{stock}' 의 테마 '{theme}' 가 오늘 스캔에 없습니다.",
                  file=sys.stderr)
            continue
        if stock not in by_theme[theme]:
            print(f"[llm_group] 무시: '{theme}' 의 조건 충족 종목에 '{stock}' 이 없습니다.",
                  file=sys.stderr)
            continue
        ok_misfits.append({
            "stock": stock, "theme": theme,
            "belongs": str(item.get("belongs") or "").strip(),
            "reason": str(item.get("reason") or "").strip(),
        })
    return ok_stale, ok_misfits


def preserve_excluded_keywords(groups: list[dict]) -> int:
    """제외가 지목한 키워드는 키워드 지도에서 지우지 않는다.

    키워드 제외는 keywords.json 의 키워드 -> 테마 지도를 거꾸로 타고 내려가
    걸린다. 그런데 그렇게 제외된 테마는 이 단계에서 모델에게 보이지도 않으므로
    모델이 만든 새 지도에는 그 키워드가 없다. 그대로 덮어쓰면 다음 단계
    (theme_scan --regroup)에서 지도가 끊겨 제외가 풀린다 — 어제 뺀 테마가
    오늘 슬롯에 돌아오고, 시트만 봐서는 이유를 알 수 없다.

    종목 제외의 keywords 범위도 같은 지도를 쓰므로 함께 살린다.
    """
    ex = ts.load_exclusions(EXCLUSIONS)
    wanted = [str(r.get("keyword") or r.get("pattern") or "").strip()
              for r in ex["keywords"]]
    for rule in ex["stocks"]:
        wanted += [str(k).strip() for k in (rule.get("keywords") or [])]
    wanted = [w for w in wanted if w]
    if not wanted:
        return 0

    try:
        old_entries = json.loads(KEYWORDS.read_text(encoding="utf-8")) if KEYWORDS.exists() else []
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(old_entries, list):
        return 0

    have = {g["keyword"] for g in groups}
    claimed = {t for g in groups for t in g["source_themes"]}
    kept = 0
    for entry in old_entries:
        kw = str(entry.get("keyword") or "").strip()
        if not kw or kw in have or not ts.matches_keyword(kw, wanted):
            continue
        themes = [t for t in entry.get("source_themes", []) if t not in claimed]
        if not themes:
            continue
        groups.append({"keyword": kw, "source_themes": themes,
                       "reason": "제외 규칙이 이 키워드를 지목하고 있어 지도를 유지한다."})
        have.add(kw)
        kept += 1
        print(f"[llm_group] 제외된 키워드 '{kw}' 의 테마 지도를 유지합니다 "
              f"({len(themes)}개 테마).", file=sys.stderr)
    return kept


def record_exclusions(stale: list[dict], misfits: list[dict], today: str) -> int:
    """제외 목록에 덧붙인다. 사람이 적은 항목은 그대로 둔다.

    keywords.json 은 매 실행 통째로 다시 쓰지만 이 파일은 그러면 안 된다.
    제외는 '이 회사는 이제 통신 회사가 아니다' 같은 사실의 기록이라 하루
    스캔 결과보다 오래 간다. 사람이 지우면 지워진 채로 두고(다시 제안되면
    다시 붙는다 — 그건 모델이 같은 판단을 되풀이한다는 뜻이므로 보이는 게
    낫다), 새 항목만 뒤에 붙인다.
    """
    # 파일을 통째로 들고 있다가 themes/stocks 만 갈아 끼운다. 사람이 파일에
    # 적어둔 다른 키(설명 주석 등)를 이 단계가 지우지 않게 하려는 것이다.
    try:
        doc = json.loads(EXCLUSIONS.read_text(encoding="utf-8")) if EXCLUSIONS.exists() else {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[llm_group] {EXCLUSIONS.name} 을 읽지 못해 제외를 기록하지 않습니다: {exc}",
              file=sys.stderr)
        return 0
    if not isinstance(doc, dict):
        print(f"[llm_group] {EXCLUSIONS.name} 의 최상위가 객체가 아닙니다 — 건너뜁니다.",
              file=sys.stderr)
        return 0
    data = ts.load_exclusions(EXCLUSIONS)
    themes, stocks = data["themes"], data["stocks"]
    added = 0

    for item in stale:
        name = item["theme"]
        # 이미 걸리는 테마면 다시 적지 않는다. 정확히 같은 이름이 아니라
        # 기존 패턴에 걸리는지를 본다 — '5G' 가 이미 있으면 '5G(5세대
        # 이동통신)' 을 또 넣을 이유가 없다.
        if ts.theme_exclusion(name, themes):
            continue
        themes.append({"pattern": name, "reason": item["reason"],
                       "by": "llm", "added": today})
        added += 1

    for item in misfits:
        stock = {"name": item["stock"], "code": ""}
        if ts.stock_exclusion(stock, item["theme"], None, stocks):
            continue
        reason = item["reason"]
        if item["belongs"]:
            reason = f"{reason} (실제로는 {item['belongs']} 쪽)"
        stocks.append({"name": item["stock"], "themes": [item["theme"]],
                       "reason": reason, "by": "llm", "added": today})
        added += 1

    if not added:
        return 0
    doc["themes"], doc["stocks"] = themes, stocks
    EXCLUSIONS.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return added


def main() -> int:
    unavailable = cli_available()
    if unavailable:
        return give_up(unavailable)
    if not RAW.exists():
        return give_up(f"{RAW.name} 이 없습니다 (theme_scan.py 를 먼저 실행)")

    raw = json.loads(RAW.read_text(encoding="utf-8"))
    if not raw:
        return give_up("오늘 조건을 충족한 테마가 0개입니다")

    # theme_scan 이 그룹을 만들 때와 같은 목록을 본다. 이미 제외된 테마를
    # 모델에게 보여주면 그것까지 묶으려 들고, 그 그룹은 regroup 단계에서
    # 조용히 사라진다 — 모델이 오늘의 슬롯을 헛되이 쓴 셈이 된다.
    scanned = len(raw)
    raw = ts.filter_themes(raw)
    if not raw:
        return give_up(f"제외 후 남은 테마가 0개입니다 (스캔 {scanned}개)")
    if scanned != len(raw):
        print(f"[llm_group] 제외 적용: {scanned}개 -> {len(raw)}개", file=sys.stderr)
    known = {t["theme_name"] for t in raw}

    prompt = (
        f"오늘 조건을 충족한 테마 {len(raw)}개입니다. "
        f"슬롯은 {TARGET_GROUPS}개뿐이니 그 안에 들어가도록 묶어 주세요.\n\n"
        + describe(raw)
    )

    text = run_claude(prompt)
    if not text:
        return give_up("CLI 호출이 결과를 주지 않았습니다")

    try:
        answer = json.loads(strip_fence(text))
        groups = validate(answer["groups"], known)
        stale, misfits = validate_exclusions(
            answer.get("stale_themes") or [], answer.get("misfit_stocks") or [], raw)
    except (StopIteration, json.JSONDecodeError, KeyError, TypeError) as exc:
        return give_up(f"응답을 읽지 못했습니다: {type(exc).__name__}: {exc}")

    if not groups:
        return give_up("검증을 통과한 그룹이 없습니다")

    preserve_excluded_keywords(groups)

    before = KEYWORDS.read_text(encoding="utf-8") if KEYWORDS.exists() else ""
    payload = json.dumps(groups, ensure_ascii=False, indent=2) + "\n"
    KEYWORDS.write_text(payload, encoding="utf-8")

    print(f"[llm_group] {len(raw)}개 테마 -> {len(groups)}개 그룹", file=sys.stderr)
    for g in groups:
        print(f"  [{g['keyword']}] {', '.join(g['source_themes'])}", file=sys.stderr)
        print(f"      {g['reason']}", file=sys.stderr)
    leftover = known - {t for g in groups for t in g["source_themes"]}
    if leftover:
        print(f"  (어느 그룹에도 안 들어감: {', '.join(sorted(leftover))})",
              file=sys.stderr)
    print(f"[llm_group] keywords.json {'변경됨' if payload != before else '동일'}",
          file=sys.stderr)

    # 제외는 keywords.json 을 쓴 다음에 기록한다. 앞에서 죽으면 오늘 그룹도
    # 없는 것이고, 그때 제외만 남기면 다음 실행이 이유 모를 구멍을 안고 시작한다.
    for item in stale:
        print(f"[llm_group] 제외 제안(테마): {item['theme']} — {item['reason']}",
              file=sys.stderr)
    for item in misfits:
        print(f"[llm_group] 제외 제안(종목): {item['stock']} @ {item['theme']} — "
              f"{item['reason']}", file=sys.stderr)
    added = record_exclusions(stale, misfits, datetime.now(KST).strftime("%Y-%m-%d"))
    print(f"[llm_group] exclusions.json 에 {added}건 추가", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
