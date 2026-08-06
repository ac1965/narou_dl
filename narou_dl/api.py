"""なろう小説API (https://dev.syosetu.com/man/api/) クライアント。

作品タイトル・作者名・あらすじ・話数などのメタデータを取得する。
本文そのものはこのAPIでは取得できないため scraper.py で別途取得する。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

API_ENDPOINT = "https://api.syosetu.com/novelapi/api/"
# ncode.syosetu.com は非ブラウザ的なUser-Agentを403で弾くため、
# 一般的なブラウザのUAを付与する(なろう小説API自体はUA不問だが統一している)。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class NovelInfo:
    ncode: str
    title: str
    writer: str
    story: str
    general_all_no: int  # 全掲載話数。0の場合は短編(1話のみ、URLに話数が付かない)
    novel_type: int      # 1: 連載, 2: 短編
    end: int             # 1: 完結, 0: 連載中

    @property
    def is_tanpen(self) -> bool:
        """短編(1話完結・URLに話数が付かない)かどうか"""
        return self.novel_type == 2 or self.general_all_no == 0

    @property
    def episode_count(self) -> int:
        return 1 if self.is_tanpen else self.general_all_no


class NarouAPIError(RuntimeError):
    pass


class NarouAPI:
    """なろう小説APIへのアクセスをまとめたクライアント"""

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.timeout = timeout

    def get_novel_info(self, ncode: str) -> NovelInfo:
        """ncode を指定して作品メタデータを取得する

        Args:
            ncode: 作品コード(例: "n9669bk")。大文字小文字どちらでも可。
        """
        params = {
            "ncode": ncode,
            "of": "t-n-w-s-ga-nt-e",  # title, ncode, writer, story, general_all_no, noveltype, end
            "out": "json",
        }
        resp = self.session.get(API_ENDPOINT, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        if not data or "allcount" not in data[0]:
            raise NarouAPIError(f"不正なAPI応答: {data!r}")
        if data[0]["allcount"] == 0:
            raise NarouAPIError(f"ncode '{ncode}' の作品が見つかりませんでした")

        entry = data[1]
        return NovelInfo(
            ncode=entry["ncode"],
            title=entry["title"],
            writer=entry["writer"],
            story=entry["story"],
            general_all_no=entry.get("general_all_no", 0),
            novel_type=entry.get("noveltype", 1),
            end=entry.get("end", 0),
        )


def polite_sleep(seconds: float = 1.0) -> None:
    """サーバー負荷軽減のための待機。テストではモックしてよい。"""
    time.sleep(seconds)
