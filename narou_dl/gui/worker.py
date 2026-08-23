"""GUIのバックグラウンドスレッドで実行するダウンロード処理。

cli.py の _download_and_build() と同じ処理内容(ebooklib/aozoraepub3
両バックエンド、--emit-aozora-txt)を、print() の代わりにQtシグナルで
ログ・進捗を通知する形に書き直したもの。ロジック自体(キャッシュ鮮度
チェック・リトライ・EPUB生成)は cli.py / aozora.py / aozoraepub3_backend.py
の各ヘルパーをそのまま再利用する。

キャンセル対応: QThreadを外部から強制終了する安全な方法は無いため、
cancel()で立てたフラグを話の取得ループの各イテレーション先頭でだけ
チェックする協調的(cooperative)キャンセルにしている。ネットワーク
リクエスト中やリトライの待機中には反応しない(次の話の境界で止まる)。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests
from PySide6.QtCore import QThread, Signal

from ..api import NarouAPI, NarouAPIError, polite_sleep
from ..aozora import build_novel_text
from ..aozoraepub3_backend import AozoraEpub3Error, build_epub_via_aozoraepub3
from ..cache import Cache
from ..cli import fetch_with_retry, sanitize_filename, _load_from_cache_if_fresh
from ..epub_builder import build_epub
from ..image_fetch import download_images_for_aozora
from ..library import LIBRARY_OPTION_KEYS, Library
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
    backend: str = "ebooklib"
    aozoraepub3_jar: Path | None = None
    device: str | None = None
    emit_aozora_txt: bool = False
    emit_aozora_txt_images: bool = False
    emit_pdf: bool = False
    library_add: bool = False


class _CancelledError(Exception):
    """ユーザーがキャンセルボタンを押して処理を中断したことを示す。"""


class _WorkerAbort(Exception):
    """ユーザーに表示すべき既知のエラーで処理を中断したことを示す。"""


class DownloadWorker(QThread):
    """1作品のダウンロード〜EPUB生成をバックグラウンドスレッドで実行する。"""

    log = Signal(str)
    progress = Signal(int, int)  # (現在, 全体)
    title_fetched = Signal(str)  # 作品タイトル(取得完了時。進捗表示のncodeに添える用)
    finished_ok = Signal(str)  # 出力先パス
    finished_error = Signal(str)  # エラーメッセージ
    finished_cancelled = Signal()

    def __init__(self, options: DownloadOptions, parent=None):
        super().__init__(parent)
        self.options = options
        self._cancelled = False

    def cancel(self) -> None:
        """次の話の取得に進む前にチェックされる中断フラグを立てる。"""
        self._cancelled = True

    def run(self) -> None:
        try:
            output_path = self._download()
        except _CancelledError:
            self.finished_cancelled.emit()
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

        self.title_fetched.emit(info.title)
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
            if self._cancelled:
                raise _CancelledError()

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

        if args.backend == "aozoraepub3":
            self._build_via_aozoraepub3(args, info, episodes, chapter_map, session, cache, output_path)
        else:
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
            if args.emit_aozora_txt:
                self._emit_aozora_txt(args, info, episodes, chapter_map, session, cache, output_path)

        if args.emit_pdf:
            self._emit_pdf(output_path)

        self.log.emit("完了しました。")

        if args.library_add:
            self._add_to_library(args, info)

        return output_path

    def _build_via_aozoraepub3(self, args, info, episodes, chapter_map, session, cache, output_path) -> None:
        work_dir = Path(output_path).resolve().parent
        work_dir.mkdir(parents=True, exist_ok=True)

        image_registry: dict[str, str] = {}
        if not args.no_images:
            self.log.emit("  挿絵をダウンロード中(AozoraEpub3向けにファイル保存)...")
            image_registry = download_images_for_aozora(
                episodes, work_dir, session=session, disk_cache=cache,
            )

        novel_text = build_novel_text(
            info.title, info.writer, info.story, episodes, chapter_map, image_registry,
        )
        txt_path = Path(output_path).with_suffix(".txt")
        txt_path.write_text(novel_text, encoding="utf-8")

        try:
            result = build_epub_via_aozoraepub3(
                txt_path,
                args.aozoraepub3_jar,
                dst_dir=work_dir,
                vertical=not args.yoko,
                cover_first_image=not args.no_images,
                device=args.device,
            )
        except AozoraEpub3Error as exc:
            raise _WorkerAbort(f"AozoraEpub3でのEPUB化に失敗しました: {exc}") from exc

        if result.epub_path != Path(output_path):
            result.epub_path.replace(output_path)
        if result.warnings:
            self.log.emit("  [警告] AozoraEpub3からの警告:")
            for w in result.warnings:
                self.log.emit(f"    {w}")

    def _emit_aozora_txt(self, args, info, episodes, chapter_map, session, cache, output_path) -> None:
        image_registry: dict[str, str] = {}
        if args.emit_aozora_txt_images:
            txt_work_dir = Path(output_path).resolve().parent
            self.log.emit("  挿絵をダウンロード中(青空文庫記法テキスト向けにファイル保存)...")
            image_registry = download_images_for_aozora(
                episodes, txt_work_dir, session=session, disk_cache=cache,
            )

        novel_text = build_novel_text(
            info.title, info.writer, info.story, episodes, chapter_map, image_registry,
        )
        txt_path = Path(output_path).with_suffix(".txt")
        txt_path.write_text(novel_text, encoding="utf-8")
        self.log.emit(f"  青空文庫記法テキストを書き出しました -> {txt_path}")

    def _emit_pdf(self, output_path) -> None:
        try:
            from ..pdf_builder import PdfEngineError, build_pdf
        except ImportError as exc:
            raise _WorkerAbort(
                "--emit-pdfにはplaywrightが必要です。プロジェクトルートで"
                'pip install -e ".[pdf]" を実行してインストールしてください'
            ) from exc
        pdf_path = Path(output_path).with_suffix(".pdf")
        self.log.emit(f"  PDFを生成中(Chromium)... -> {pdf_path}")
        try:
            build_pdf(output_path, pdf_path)
        except PdfEngineError as exc:
            raise _WorkerAbort(
                f"PDF生成に失敗しました: {exc}\n"
                "Chromium本体が未インストールの場合は "
                "python -m playwright install chromium を実行してください"
            ) from exc

    def _add_to_library(self, args, info) -> None:
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        options = {key: getattr(args, key) for key in LIBRARY_OPTION_KEYS}
        options["aozoraepub3_jar"] = (
            str(options["aozoraepub3_jar"]) if options["aozoraepub3_jar"] else None
        )
        Library(cache_dir).add(args.ncode, info.title, options)
        self.log.emit(f"ライブラリに登録しました: {info.title} ({args.ncode})")
