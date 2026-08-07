"""aozoraepub3 バックエンド用に、本文中の挿絵をローカルファイルとして
保存し、青空文庫注記(aozora.img_to_aozora)が参照する url -> 相対パス の
対応表(image_registry)を作る。

ebooklibバックエンドの _ImageEmbedder(epub_builder.py)がEPUB内部への
同梱まで行うのに対し、こちらは「ファイルとしてディスクに保存するだけ」に
とどめる。これは Ruby版 narou の illustration.rb が担っている役割
(ダウンロードして絶対パスを返す。EPUB化自体はAozoraEpub3に任せる)と同じ
分担にしている。
"""
from __future__ import annotations

import mimetypes
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from .api import USER_AGENT

if TYPE_CHECKING:
    from .cache import Cache
    from .scraper import Episode

_IMG_SRC_RE = re.compile(r'<img src="([^"]*)"')

# narou.rb の illustration.rb の ILLUST_DIR = "挿絵/" に合わせる
ILLUST_DIR_NAME = "挿絵"


def _iter_image_urls(episodes: list["Episode"]):
    seen: set[str] = set()
    for ep in episodes:
        for paragraph in ep.paragraphs:
            for m in _IMG_SRC_RE.finditer(paragraph):
                url = m.group(1)
                if url not in seen:
                    seen.add(url)
                    yield url


def download_images_for_aozora(
    episodes: list["Episode"],
    dst_dir: Path,
    session: requests.Session | None = None,
    disk_cache: "Cache | None" = None,
) -> dict[str, str]:
    """本文中の全挿絵URLをダウンロードし、url -> 相対パス の対応表を返す

    Args:
        episodes: 話データのリスト。
        dst_dir: 青空文庫記法テキストを置くディレクトリ(挿絵/ ディレクトリを
            この直下に作成する。AozoraEpub3はテキストからの相対パスで
            画像を解決するため)。
        session: ダウンロードに使う requests.Session(省略時は新規作成)。
        disk_cache: 指定するとダウンロード済み画像データを再利用する。

    Returns:
        url -> "挿絵/illust_0001.jpg" 形式の相対パスの対応表。
        ダウンロードに失敗したURLはこの対応表に含まれない
        (aozora.img_to_aozora側でタグごと削除される)。
    """
    session = session or requests.Session()
    if "User-Agent" not in session.headers:
        session.headers["User-Agent"] = USER_AGENT

    illust_dir = dst_dir / ILLUST_DIR_NAME
    illust_dir.mkdir(parents=True, exist_ok=True)

    registry: dict[str, str] = {}
    count = 0

    for url in _iter_image_urls(episodes):
        cached = disk_cache.load_image(url) if disk_cache else None
        if cached is not None:
            content, content_type = cached
        else:
            try:
                resp = session.get(url, timeout=15)
                resp.raise_for_status()
                content = resp.content
                content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            except (requests.RequestException, OSError) as exc:
                print(f"  [警告] 挿絵のダウンロードに失敗しました ({url}): {exc}", file=sys.stderr)
                continue
            if disk_cache:
                disk_cache.save_image(url, content, content_type)

        count += 1
        ext = mimetypes.guess_extension(content_type) or ".jpg"
        if ext == ".jpe":
            ext = ".jpg"
        file_name = f"illust_{count:04d}{ext}"
        (illust_dir / file_name).write_bytes(content)
        # AozoraEpub3の注記に埋め込むのはテキストファイルからの相対パス
        registry[url] = f"{ILLUST_DIR_NAME}/{file_name}"

    return registry
