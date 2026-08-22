"""追跡中の作品一覧(ライブラリ)を管理するモジュール。

`narou-dl <ncode> --library-add` で作品をライブラリに登録すると、その時点の
ダウンロードオプション(縦横・章立て・挿絵有無・バックエンド等)が記憶される。
以後 `narou-dl --update-all` で登録済み全作品をまとめて再取得できる。

実際の再取得処理自体は cli.py の _download_and_build() をそのまま再利用する
(ライブラリはオプションの記憶と一覧管理のみを担当する)。新規話・改稿された
話だけが実際に取得されるのは cache.py の鮮度判定によるもので、ライブラリ側は
それに関与しない。

保存先はキャッシュディレクトリ直下の library.json
(<cache_dir>/library.json。作品ごとのキャッシュである <cache_dir>/<ncode>/
とは兄弟関係になる)。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .cache import default_cache_dir

LIBRARY_OPTION_KEYS = (
    "output",
    "sleep",
    "yoko",
    "no_chapters",
    "no_images",
    "no_update_check",
    "backend",
    "aozoraepub3_jar",
    "device",
    "emit_aozora_txt",
    "emit_aozora_txt_images",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class LibraryEntry:
    """ライブラリに登録された1作品分の情報。"""

    ncode: str
    title: str
    added_at: str
    options: dict = field(default_factory=dict)


class Library:
    """library.jsonの読み書きを行うクラス。"""

    def __init__(self, cache_dir: Path | None = None):
        """
        Args:
            cache_dir: キャッシュのルートディレクトリ。省略時は
                cache.default_cache_dir() の結果を使う。
        """
        base = cache_dir or default_cache_dir()
        self.path = base / "library.json"

    def load(self) -> dict[str, LibraryEntry]:
        """登録済みの全作品を読み込む。

        Returns:
            ncode(小文字) -> LibraryEntry の対応表。ファイルが無い、または
            壊れている場合は空の dict。
        """
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return {k: LibraryEntry(**v) for k, v in data.items()}
        except (json.JSONDecodeError, TypeError, KeyError):
            return {}

    def _save(self, entries: dict[str, LibraryEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: asdict(v) for k, v in entries.items()}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add(self, ncode: str, title: str, options: dict) -> LibraryEntry:
        """作品をライブラリに登録する(既存なら上書きする)。

        Args:
            ncode: 作品コード。
            title: 作品タイトル(一覧表示用)。
            options: LIBRARY_OPTION_KEYS のうち記憶したい値の対応表。

        Returns:
            登録したエントリ。
        """
        ncode = ncode.lower()
        entries = self.load()
        entry = LibraryEntry(ncode=ncode, title=title, added_at=_now_iso(), options=options)
        entries[ncode] = entry
        self._save(entries)
        return entry

    def remove(self, ncode: str) -> bool:
        """作品をライブラリから削除する。

        Returns:
            削除できれば True、元々登録されていなければ False。
        """
        ncode = ncode.lower()
        entries = self.load()
        if ncode not in entries:
            return False
        del entries[ncode]
        self._save(entries)
        return True
