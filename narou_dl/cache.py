"""取得済みデータをディスクにキャッシュし、再ダウンロードを避けるためのモジュール。

キャッシュの保存先は既定で以下の優先順位により決まる(cli.py の --cache-dir で
明示指定された場合はそちらが最優先):

  1. 環境変数 XDG_CACHE_HOME が設定されていれば `$XDG_CACHE_HOME/narou-dl`
     (典型的には ~/.cache/narou-dl)
  2. それ以外はプロジェクト内(カレントディレクトリ) `./.narou-dl-cache`

作品ごとに以下の構成でキャッシュを持つ:

    <cache_dir>/<ncode>/
        info.json           作品メタデータ(参考用。取得のたびに上書きされる)
        chapters.json       章立て(話数->章タイトル)。総話数が変わると無効化される
        episodes/0001.json  各話の本文(話数ごとに1ファイル)
        images/<hash>       挿絵の実データ(URLのハッシュをファイル名にする)
        images/<hash>.meta  対応するContent-Type

話が「改稿」で更新される場合など、キャッシュが古くなることがあるため、
明示的に --refresh で無視させることができる。

追加時期: v1.1.0 で新規追加されたモジュール。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from .api import NovelInfo
from .scraper import Episode


def default_cache_dir() -> Path:
    """キャッシュの既定保存先を決定する

    優先順位:
      1. 環境変数 XDG_CACHE_HOME が設定されていれば `$XDG_CACHE_HOME/narou-dl`
         (例: ~/.cache/narou-dl)
      2. それ以外はプロジェクト内(カレントディレクトリ) `./.narou-dl-cache`

    Returns:
        キャッシュの既定保存先ディレクトリ。
    """
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "narou-dl"
    return Path.cwd() / ".narou-dl-cache"


class Cache:
    """作品単位のローカルキャッシュを扱うクラス"""

    def __init__(self, ncode: str, cache_dir: Path | None = None):
        """
        Args:
            ncode: 作品コード。大文字小文字は区別せず小文字に正規化される。
            cache_dir: キャッシュの保存先ディレクトリ。省略時は
                default_cache_dir() の結果を使う。
        """
        self.ncode = ncode.lower()
        base = cache_dir or default_cache_dir()
        self.root = base / self.ncode
        self.episodes_dir = self.root / "episodes"
        self.images_dir = self.root / "images"

    # --- 作品メタデータ ---
    def load_info(self) -> NovelInfo | None:
        """キャッシュ済みの作品メタデータを読み込む。

        Returns:
            キャッシュがあれば NovelInfo、なければ(壊れている場合を含む)None。
        """
        path = self.root / "info.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return NovelInfo(**data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    def save_info(self, info: NovelInfo) -> None:
        """作品メタデータをキャッシュに保存する。

        Args:
            info: 保存する作品メタデータ。
        """
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "info.json").write_text(
            json.dumps(asdict(info), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- 章立て ---
    def load_chapter_map(self, total_episodes: int) -> dict[int, str] | None:
        """キャッシュ済みの章立てを読み込む。

        Args:
            total_episodes: 現在の全話数。保存時の値と異なる場合、新しい話が
                投稿された等の理由でキャッシュが古い可能性があるため無効
                (None)として扱う。

        Returns:
            話数 -> 章タイトル の対応表。キャッシュが無い/無効なら None。
        """
        path = self.root / "chapters.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("total_episodes") != total_episodes:
                return None
            return {int(k): v for k, v in data["chapter_map"].items()}
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def save_chapter_map(self, chapter_map: dict[int, str], total_episodes: int) -> None:
        """章立てをキャッシュに保存する。

        Args:
            chapter_map: 話数(1始まり) -> 章タイトル の対応表。
            total_episodes: 保存時点での全話数(次回読み込み時の有効性判定に使う)。
        """
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "total_episodes": total_episodes,
            "chapter_map": {str(k): v for k, v in chapter_map.items()},
        }
        (self.root / "chapters.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- 話本文 ---
    def _episode_path(self, episode_no: int) -> Path:
        return self.episodes_dir / f"{episode_no:04d}.json"

    def load_episode(self, episode_no: int) -> Episode | None:
        """キャッシュ済みの話本文を読み込む。

        Args:
            episode_no: 話数(1始まり)。

        Returns:
            キャッシュがあれば Episode、なければ(壊れている場合を含む)None。
        """
        path = self._episode_path(episode_no)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Episode(**data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    def save_episode(self, episode: Episode) -> None:
        """話本文をキャッシュに保存する。

        Args:
            episode: 保存する話データ(episode.index がファイル名に使われる)。
        """
        self.episodes_dir.mkdir(parents=True, exist_ok=True)
        self._episode_path(episode.index).write_text(
            json.dumps(asdict(episode), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- 挿絵画像 ---
    @staticmethod
    def _image_key(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]

    def load_image(self, url: str) -> tuple[bytes, str] | None:
        """キャッシュ済みの挿絵データを読み込む。

        Args:
            url: 挿絵の元URL。

        Returns:
            (画像バイナリ, Content-Type) のタプル。キャッシュが無ければ None。
        """
        key = self._image_key(url)
        meta_path = self.images_dir / f"{key}.meta"
        data_path = self.images_dir / key
        if not (meta_path.exists() and data_path.exists()):
            return None
        content_type = meta_path.read_text(encoding="utf-8").strip()
        return data_path.read_bytes(), content_type

    def save_image(self, url: str, content: bytes, content_type: str) -> None:
        """挿絵データをキャッシュに保存する。

        Args:
            url: 挿絵の元URL(キャッシュキーの生成に使う)。
            content: 画像バイナリデータ。
            content_type: 画像のMIMEタイプ(例: "image/jpeg")。
        """
        self.images_dir.mkdir(parents=True, exist_ok=True)
        key = self._image_key(url)
        (self.images_dir / key).write_bytes(content)
        (self.images_dir / f"{key}.meta").write_text(content_type, encoding="utf-8")

    # --- 管理 ---
    def clear(self) -> None:
        """この作品のキャッシュ(メタデータ・本文・章立て・挿絵)を全て削除する。"""
        if self.root.exists():
            shutil.rmtree(self.root)
