"""GUIのバックグラウンドスレッドで実行するダウンロード処理。

cli.py の run() と同じ処理内容を、print() の代わりにQtシグナルで
ログ・進捗を通知する形に書き直したもの。ロジック自体(キャッシュ鮮度
チェック・リトライ・EPUB生成)は cli.py の各ヘルパーをそのまま再利用する。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests
from PySide6.QtCore import QThread, Signal

from ..api import NarouAPI, NarouAPIError, polite_sleep
from ..cache import Cache
from ..cli import fetch_with_retry, sanitize_filename, _load_from_cache_if_fresh
from ..epub_builder import build_epub
from ..scraper import EpisodeScraper, ScrapeError, TocEntry


@dataclass
class DownloadOptions:
    """GUIから渡すダウンロード設定。cli.pyのargparse.Namespaceに相当する。"""

    ncode: str
    output: str | None = None
    sleep: float = 1.0
    start: int = 1
    end: int | None = None
    yoko: bool = False
    no_chapters: bool = False
    no_images: bool = False
    no_cache: bool = False
    refresh: bool = False
    clear_cache: bool = False
    cache_dir: str | None = None
    no_update_check: bool = False


class DownloadWorker(QThread):
    """1作品のダウンロード〜EPUB生成をバックグラウンドスレッドで実行する。"""

    log = Signal(str)
    progress = Signal(int, int)  # (現在, 全体)
    finished_ok = Signal(str)  # 出力先パス
    finished_error = Signal(str)  # エラーメッセージ

    def __init__(self, options: DownloadOptions, parent=None):
        super().__init__(parent)
        self.options = options

    def run(self) -> None:
        try:
            output_path = self._download()
        except _WorkerAbort as exc:
            self.finished_error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - GUIに任意の例外を表示するため
            self.finished_error.emit(f"予期しないエラー: {exc}")
        else:
            self.finished_ok.emit(output_path)

    def _download(self) -> str:
        args = self.options

        cache: Cache | None = None
        if not args.no_cache:
            cache_dir = Path(args.cache_dir) if args.cache_dir else None
            cache = Cache(args.ncode, cache_dir=cache_dir)
            if args.clear_cache:
                cache.clear()
                self.log.emit("キャッシュを削除しました。")

        session = requests.Session()
        api = NarouAPI(session=session)
        scraper = EpisodeScraper(session=session)

        self.log.emit(f"作品情報を取得中... (ncode={args.ncode})")
        try:
            info = api.get_novel_info(args.ncode)
        except (NarouAPIError, requests.RequestException) as exc:
            raise _WorkerAbort(f"作品情報の取得に失敗しました: {exc}") from exc
        if cache:
            cache.save_info(info)

        self.log.emit(f"タイトル: {info.title}")
        self.log.emit(f"作者: {info.writer}")
        self.log.emit(
            f"話数: {info.episode_count}話 ({'短編' if info.is_tanpen else '連載'})"
        )

        need_toc = not info.is_tanpen and (
            not args.no_chapters or (cache is not None and not args.no_update_check)
        )
        toc: dict[int, TocEntry] = {}
        chapter_map: dict[int, str] = {}
        if need_toc:
            self.log.emit("目次を取得中...")
            try:
                toc = fetch_with_retry(
                    "目次", lambda: scraper.fetch_toc(args.ncode, info.general_all_no)
                )
            except (requests.RequestException, ScrapeError) as exc:
                self.log.emit(f"  [警告] 目次の取得に失敗しました: {exc}")
                toc = {}
            if not args.no_chapters:
                chapter_map = {i: e.chapter_title for i, e in toc.items() if e.chapter_title}
                if chapter_map:
                    n_chapters = len(set(chapter_map.values()))
                    self.log.emit(f"  {n_chapters}章を検出しました。")

        if info.is_tanpen:
            episode_numbers: list[int | None] = [None]
        else:
            end = args.end or info.general_all_no
            episode_numbers = list(range(args.start, end + 1))

        episodes = []
        total = len(episode_numbers)
        cache_hits = 0
        for i, ep_no in enumerate(episode_numbers, start=1):
            label = f"{ep_no}話" if ep_no else "(短編)"
            self.progress.emit(i, total)

            cached_episode = _load_from_cache_if_fresh(cache, args, toc, ep_no)
            if cached_episode is not None:
                self.log.emit(f"  [{i}/{total}] {label} をキャッシュから読み込みました。")
                episodes.append(cached_episode)
                cache_hits += 1
                continue

            self.log.emit(f"  [{i}/{total}] {label} を取得中...")
            try:
                episode = fetch_with_retry(label, lambda: scraper.fetch_episode(args.ncode, ep_no))
            except (requests.RequestException, ScrapeError) as exc:
                raise _WorkerAbort(f"{label} の取得に失敗しました: {exc}") from exc
            episodes.append(episode)
            if cache:
                updated_at = toc[ep_no].updated_at if ep_no in toc else None
                cache.save_episode(episode, updated_at=updated_at)
            if i < total:
                polite_sleep(args.sleep)

        if cache_hits:
            self.log.emit(f"({cache_hits}/{total}話をキャッシュから読み込みました)")

        if args.output:
            output_path = args.output
            if Path(output_path).suffix.lower() != ".epub":
                output_path += ".epub"
        else:
            output_path = f"{sanitize_filename(info.title)}.epub"

        self.log.emit(
            f"EPUBを生成中... ({'横書き' if args.yoko else '縦書き'}) -> {output_path}"
        )
        build_epub(
            info,
            episodes,
            output_path,
            vertical=not args.yoko,
            chapter_map=chapter_map,
            embed_images=not args.no_images,
            session=session,
            disk_cache=cache,
        )
        self.log.emit("完了しました。")
        return output_path


class _WorkerAbort(Exception):
    """ユーザーに表示すべき既知のエラーで処理を中断したことを示す。"""
