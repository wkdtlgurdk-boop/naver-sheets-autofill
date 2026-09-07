"""Thin Google Sheets wrapper for the scheduled autofill jobs.

Replaces the old approach of driving Chrome and pasting through the
clipboard. That existed only because typing Korean into a live Sheets grid
drops characters; writing through the API sidesteps the grid entirely, so
the IME race, the clipboard round-trip, and the need for a logged-in
browser on a powered-on PC all go away together.

Auth: a service account, with its JSON key supplied whole in the
GOOGLE_SERVICE_ACCOUNT_JSON environment variable (a GitHub Actions secret).
Each target spreadsheet must be shared with the service account's email as
an editor — the account has no rights it is not explicitly given.
"""
from __future__ import annotations

import json
import os

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Sheets answers 503 often enough to matter for an unattended daily job: the
# first real run on the runner died on one, halfway through, with the Daily
# sheet already written and Monthly untouched. execute(num_retries=) retries
# 5xx with exponential backoff, which turns that class of failure into a pause.
# Reads and value writes are safe to repeat — every write here overwrites a
# fixed range with fixed values. Tab creation deliberately does not retry.
API_RETRIES = 4

# google-api-python-client 는 이 모듈을 실제로 쓸 때만 부른다. 덕분에
# test_theme_write.py 가 가짜 시트로 돌 때는 구글 라이브러리가 없어도 된다.


def _credentials():
    from google.oauth2.service_account import Credentials

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise SystemExit(
            "[중단] GOOGLE_SERVICE_ACCOUNT_JSON 이 비어 있습니다. "
            "GitHub Actions secret 또는 로컬 환경변수를 확인하세요."
        )
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[중단] 서비스 계정 JSON 파싱 실패: {exc}") from exc
    return Credentials.from_service_account_info(info, scopes=SCOPES)


class Spreadsheet:
    """One spreadsheet, with gid -> tab-title resolution done up front.

    A1 ranges name a tab by title, but every anchor we have on file is
    recorded as a gid (tab titles get renamed, gids do not), so the mapping
    is fetched once per run and cached.
    """

    def __init__(self, spreadsheet_id: str):
        from googleapiclient.discovery import build

        self.id = spreadsheet_id
        self._api = build("sheets", "v4", credentials=_credentials(),
                          cache_discovery=False).spreadsheets()
        meta = self._api.get(spreadsheetId=spreadsheet_id,
                             fields="sheets.properties").execute(num_retries=API_RETRIES)
        self._by_gid = {
            s["properties"]["sheetId"]: s["properties"]["title"]
            for s in meta.get("sheets", [])
        }

    def title_for_gid(self, gid: int) -> str:
        try:
            return self._by_gid[gid]
        except KeyError:
            raise SystemExit(
                f"[중단] gid {gid} 인 탭이 스프레드시트 {self.id} 에 없습니다. "
                f"현재 탭: {sorted(self._by_gid.items())}"
            ) from None

    def gid_for_title(self, title: str) -> int | None:
        """gid by tab title, or None. Titles are what change month to month.

        The reverse of title_for_gid, and the direction this code now works
        in: a gid recorded in the source outlives the tab it named (the
        hardcoded DAILY_GID silently pointed at August all through September),
        whereas a title derived from today's date cannot go stale.
        """
        for gid, name in self._by_gid.items():
            if name == title:
                return gid
        return None

    def titles(self) -> list[str]:
        return sorted(self._by_gid.values())

    def duplicate_tab(self, source_gid: int, new_title: str) -> int:
        """Copy a tab (merges, formats and formulas included) under a new name.

        Used to open the next month from the Daily template. The copy is made
        through the API rather than rebuilt cell by cell because the calendar
        blocks carry merged date cells and percent formats that a value-only
        write cannot reproduce.
        """
        if self.gid_for_title(new_title) is not None:
            raise SystemExit(f"[중단] '{new_title}' 탭이 이미 있습니다.")
        resp = self._api.batchUpdate(
            spreadsheetId=self.id,
            body={"requests": [{"duplicateSheet": {
                "sourceSheetId": source_gid,
                "newSheetName": new_title,
                "insertSheetIndex": 0,
            }}]},
        ).execute()
        props = resp["replies"][0]["duplicateSheet"]["properties"]
        gid = int(props["sheetId"])
        self._by_gid[gid] = props["title"]
        return gid

    @staticmethod
    def _quote(title: str) -> str:
        return "'" + title.replace("'", "''") + "'"

    def read(self, gid: int, a1: str) -> list[list]:
        """Values in a range, as a ragged list of rows (trailing blanks trimmed)."""
        rng = f"{self._quote(self.title_for_gid(gid))}!{a1}"
        resp = self._api.values().get(
            spreadsheetId=self.id, range=rng,
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute(num_retries=API_RETRIES)
        return resp.get("values", [])

    def read_formatted(self, gid: int, a1: str) -> list[list]:
        """Values as the sheet displays them.

        Needed for the calendars' date column: a cell showing "8/28" may be
        stored as a real date (a serial number under UNFORMATTED_VALUE), and
        it is the displayed form we are matching against.
        """
        rng = f"{self._quote(self.title_for_gid(gid))}!{a1}"
        resp = self._api.values().get(
            spreadsheetId=self.id, range=rng,
            valueRenderOption="FORMATTED_VALUE",
        ).execute(num_retries=API_RETRIES)
        return resp.get("values", [])

    def read_formulas(self, gid: int, a1: str) -> list[list]:
        """Same, but formulas come back as their text rather than their result.

        Used to confirm a template row still holds its AVERAGEA/SUM formulas
        before writing anything near it.
        """
        rng = f"{self._quote(self.title_for_gid(gid))}!{a1}"
        resp = self._api.values().get(
            spreadsheetId=self.id, range=rng, valueRenderOption="FORMULA",
        ).execute(num_retries=API_RETRIES)
        return resp.get("values", [])

    def write(self, gid: int, a1: str, values: list[list]) -> int:
        """Write a block anchored at a1. Returns the number of cells updated."""
        rng = f"{self._quote(self.title_for_gid(gid))}!{a1}"
        resp = self._api.values().update(
            spreadsheetId=self.id, range=rng,
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute(num_retries=API_RETRIES)
        return int(resp.get("updatedCells", 0))
