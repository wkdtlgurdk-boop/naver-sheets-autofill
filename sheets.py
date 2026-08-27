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
                             fields="sheets.properties").execute()
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

    @staticmethod
    def _quote(title: str) -> str:
        return "'" + title.replace("'", "''") + "'"

    def read(self, gid: int, a1: str) -> list[list]:
        """Values in a range, as a ragged list of rows (trailing blanks trimmed)."""
        rng = f"{self._quote(self.title_for_gid(gid))}!{a1}"
        resp = self._api.values().get(
            spreadsheetId=self.id, range=rng,
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
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
        ).execute()
        return resp.get("values", [])

    def read_formulas(self, gid: int, a1: str) -> list[list]:
        """Same, but formulas come back as their text rather than their result.

        Used to confirm a template row still holds its AVERAGEA/SUM formulas
        before writing anything near it.
        """
        rng = f"{self._quote(self.title_for_gid(gid))}!{a1}"
        resp = self._api.values().get(
            spreadsheetId=self.id, range=rng, valueRenderOption="FORMULA",
        ).execute()
        return resp.get("values", [])

    def write(self, gid: int, a1: str, values: list[list]) -> int:
        """Write a block anchored at a1. Returns the number of cells updated."""
        rng = f"{self._quote(self.title_for_gid(gid))}!{a1}"
        resp = self._api.values().update(
            spreadsheetId=self.id, range=rng,
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()
        return int(resp.get("updatedCells", 0))
