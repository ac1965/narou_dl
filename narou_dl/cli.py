"""コマンドラインエントリポイント

使い方 (インストール後)::

    narou-dl N9669BK
    narou-dl N9669BK -o musyoku.epub --sleep 1.5
    narou-dl N9669BK --start 1 --end 20   # 一部の話だけ取得
    narou-dl N9669BK --yoko               # 横書きで生成(既定は縦書き)
    narou-dl N9669BK --no-chapters        # 章立てを無視してフラットな目次にする
    narou-dl N9669BK --no-images          # 挿絵を埋め込まない
    narou-dl N9669BK --refresh            # キャッシュを無視して取り直す
    narou-dl N9669BK --no-cache           # キャッシュを使わない
    narou-dl N9669BK --clear-cache        # この作品のキャッシュを削除してから取得する
    narou-dl N9669BK --no-update-check    # 改稿の自動検知をせず、キャッシュがあれば常に使う
    narou-dl N9669BK --backend aozoraepub3 --aozoraepub3-jar /path/to/AozoraEpub3.jar
                                           # 青空文庫記法を経由し、AozoraEpub3(改造版)の
                                           # 組版(傍点・外字・縦中横・画像回り込み等)でEPUB化する
    export AOZORAEPUB3_JAR=~/.local/share/aozoraepub3/AozoraEpub3.jar
    narou-dl N9669BK --backend aozoraepub3
                                           # --aozoraepub3-jar を毎回指定する代わりに
                                           # 環境変数 AOZORAEPUB3_JAR を使う
    narou-dl N9669BK --emit-aozora-txt    # ebooklibでEPUBを生成しつつ、同じ話データから
                                           # 青空文庫記法テキストも書き出す(挿絵注記は既定で含めない)
    narou-dl N9669BK --emit-aozora-txt --emit-aozora-txt-images
                                           # 上記に加え、挿絵をダウンロードして挿絵注記も含める

既定では取得した本文・章立て・挿絵はキャッシュディレクトリに保存され、
同じ作品を再度ダウンロードする際はキャッシュから読み込んでネットワークアクセスを省略する。
キャッシュディレクトリは --cache-dir > 環境変数 XDG_CACHE_HOME > カレントディレクトリの
./.narou-dl-cache の優先順位で決まる(詳細は cache.py を参照)。

キャッシュ利用時は既定で目次から話ごとの最終更新日時を取得し、なろう側で
本文が「改稿」された話だけを自動的に再取得する(--no-update-check で無効化可能)。

または pip install せずに直接実行::

    python -m narou_dl N9669BK

追加時期::

    v1.0.0  基本のCLI(ncode指定、-o/--sleep/--start/--end/--yoko)
    v1.1.0  --no-chapters/--no-images/--refresh/--no-cache/--clear-cache/--cache-dir
            オプションを追加(章立て・ルビ・挿絵・ローカルキャッシュ対応に伴う)
    v1.2.0  --backend {ebooklib,aozoraepub3} / --aozoraepub3-jar / --device を追加。
            aozoraepub3 バックエンドは本文をaozora.pyで青空文庫記法テキストに
            変換したうえでAozoraEpub3(改造版)の.jarを外部プロセス起動してEPUB化する。
            AozoraEpub3側の高度な組版(傍点・外字自動判定・縦中横・画像の自動回転や
            余白除去等)をそのまま利用できる代わりに、JRE(Java 21以降推奨)と
            AozoraEpub3.jar本体が別途必要になる。
    v1.3.0  --emit-aozora-txt / --emit-aozora-txt-images を追加。ebooklibバックエンドで
            EPUBを生成する際、同じ話データ(Episode.paragraphs)から独立して
            aozora.build_novel_text()を呼び、青空文庫記法テキストも書き出せるように
            した(build_epub()自体はaozora記法を経由しないため、この呼び出しは
            EPUB生成本体とは無関係に追加している)。挿絵注記は既定で含めず、
            --emit-aozora-txt-images指定時のみdownload_images_for_aozora()で
            ダウンロードして含める。--backend aozoraepub3 は既定でtxtを生成する
            ため、--emit-aozora-txt との併用はparser.error()で拒否する。

修正履歴::

    Ruby版 narou を参考に、話ごとの最終更新日時(改稿検知)による自動的な
    キャッシュ鮮度チェックを追加した(--no-update-check で従来の
    「キャッシュがあれば常に使う」挙動に戻せる)。

    aozoraepub3 バックエンド追加にあたり、scraper.py の
    _INLINE_ALLOWED_TAGS に "em" を加え、なろうの傍点表現
    (<em class="emphasisDots">)を保持するようにした。従来の
    ebooklibバックエンドはこのタグを無視するため挙動に影響しない。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import requests

from .api import NarouAPI, NarouAPIError, polite_sleep
from .aozora import build_novel_text
from .aozoraepub3_backend import AozoraEpub3Error, build_epub_via_aozoraepub3
from .cache import Cache
from .epub_builder import build_epub
from .image_fetch import download_images_for_aozora
from .scraper import Episode, EpisodeScraper, ScrapeError, TocEntry

MAX_RETRIES = 3


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def fetch_with_retry(label: str, func, retries: int = MAX_RETRIES):
    """指定した取得処理をリトライ付きで実行する共通ヘルパー"""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return func()
        except (requests.RequestException, ScrapeError) as exc:
            last_exc = exc
            wait = 2 ** attempt
            print(
                f"  [警告] {label}の取得に失敗 ({exc})."
                f" {wait}秒待って再試行します ({attempt}/{retries})",
                file=sys.stderr,
            )
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def _load_from_cache_if_fresh(
    cache: Cache | None,
    args: argparse.Namespace,
    toc: dict[int, TocEntry],
    ep_no: int | None,
) -> Episode | None:
    """キャッシュ済みかつ(鮮度チェック対象なら)最新の話だけを返す

    以下のいずれかに該当する場合は None を返し、呼び出し側に再取得させる:
      - キャッシュ自体が無効、または --refresh 指定時
      - 短編(ep_no is None、キャッシュ非対応)
      - キャッシュにその話が無い
      - 鮮度チェック対象で、目次上の最終更新日時がキャッシュ保存時と異なる
        (=改稿された可能性がある)
    """
    if cache is None or args.refresh or ep_no is None:
        return None
    cached_episode = cache.load_episode(ep_no)
    if cached_episode is None:
        return None
    if args.no_update_check or ep_no not in toc:
        return cached_episode
    if cache.load_episode_updated_at(ep_no) == toc[ep_no].updated_at:
        return cached_episode
    return None  # 改稿の可能性があるため再取得させる


def run(argv: list[str] | None = None) -> int:
    """CLIのエントリポイント本体(引数解析からEPUB書き出しまで)。

    Args:
        argv: コマンドライン引数のリスト。省略時は sys.argv から取得される
            (argparseの既定動作)。

    Returns:
        終了コード。成功時は0、失敗時は1。
    """
    parser = argparse.ArgumentParser(
        description="小説家になろうの作品をダウンロードしてEPUBに変換する"
    )
    parser.add_argument("ncode", help="作品コード (例: N9669BK)")
    parser.add_argument(
        "-o", "--output",
        help="出力ファイル名 (省略時は作品タイトルから自動生成。.epub拡張子が無ければ自動付与する)",
    )
    parser.add_argument(
        "--sleep", type=float, default=1.0, help="各話取得後の待機秒数 (既定: 1.0秒、サーバー負荷軽減のため)"
    )
    parser.add_argument("--start", type=int, default=1, help="開始話数 (既定: 1)")
    parser.add_argument("--end", type=int, default=None, help="終了話数 (既定: 最終話)")
    parser.add_argument(
        "--yoko", action="store_true", help="横書きで生成する (既定は縦書き)"
    )
    parser.add_argument(
        "--no-chapters",
        action="store_true",
        help="章立て(「第一章」などの区切り)を取得せず、フラットな目次にする",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="本文中の挿絵をダウンロード・埋め込みせず、取り除く",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="キャッシュを使わず、常にネットワークから取得する"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="キャッシュがあっても無視し、取り直してキャッシュを更新する"
    )
    parser.add_argument(
        "--clear-cache", action="store_true", help="この作品のキャッシュを削除してから取得する"
    )
    parser.add_argument(
        "--cache-dir",
        help=(
            "キャッシュの保存先ディレクトリ (既定: 環境変数 XDG_CACHE_HOME が設定されて"
            "いれば $XDG_CACHE_HOME/narou-dl、未設定ならカレントディレクトリの "
            "./.narou-dl-cache)"
        ),
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="話ごとの改稿自動検知をせず、キャッシュがあれば(--refresh指定時を除き)常に使う",
    )
    parser.add_argument(
        "--backend",
        choices=["ebooklib", "aozoraepub3"],
        default="ebooklib",
        help=(
            "EPUB化バックエンド。既定は ebooklib(narou_dl自身がHTMLを直接EPUB化する)。"
            "タイトル・著者はOPFメタデータのみに持たせ、目次はEPUB標準のTOC機能経由で"
            "参照する構成でAozoraEpub3側と揃えてあるため、通常はebooklibで十分な品質になる。"
            "aozoraepub3 は本文を青空文庫記法に変換し、AozoraEpub3(改造版)の外部プロセスで"
            "EPUB化する(傍点・外字・縦中横・画像回り込み等、より高度な組版が必要な場合に指定する)"
        ),
    )
    parser.add_argument(
        "--aozoraepub3-jar",
        type=Path,
        default=(Path(os.environ["AOZORAEPUB3_JAR"]) if os.environ.get("AOZORAEPUB3_JAR") else None),
        help=(
            "--backend aozoraepub3 使用時に必須。AozoraEpub3(改造版)の.jarへのパス。"
            "未指定時は環境変数 AOZORAEPUB3_JAR を使う"
        ),
    )
    parser.add_argument(
        "--device",
        default=None,
        help="--backend aozoraepub3 使用時のみ有効。AozoraEpub3のデバイス最適化オプション(例: kindle)",
    )
    parser.add_argument(
        "--emit-aozora-txt",
        action="store_true",
        help=(
            "EPUB(ebooklibバックエンド)生成に加え、同じ話データから青空文庫記法の"
            "テキストファイル(出力ファイル名の拡張子を.txtにしたもの)も書き出す。"
            "--backend aozoraepub3 とは併用不可(そちらは既定でtxtを生成するため)"
        ),
    )
    parser.add_argument(
        "--emit-aozora-txt-images",
        action="store_true",
        help=(
            "--emit-aozora-txt 使用時、挿絵もダウンロードして青空文庫記法の"
            "挿絵注記を含める(既定では挿絵注記は含めない)"
        ),
    )
    args = parser.parse_args(argv)

    if args.backend == "aozoraepub3" and not args.aozoraepub3_jar:
        parser.error(
            "--backend aozoraepub3 を指定する場合は --aozoraepub3-jar "
            "または環境変数 AOZORAEPUB3_JAR の指定が必須です"
        )

    if args.emit_aozora_txt and args.backend == "aozoraepub3":
        parser.error(
            "--emit-aozora-txt は --backend aozoraepub3 と併用できません"
            "(aozoraepub3 バックエンドは既に青空文庫記法テキストを生成します)"
        )

    cache: Cache | None = None
    if not args.no_cache:
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        cache = Cache(args.ncode, cache_dir=cache_dir)
        if args.clear_cache:
            cache.clear()
            print("キャッシュを削除しました。")

    session = requests.Session()
    api = NarouAPI(session=session)
    scraper = EpisodeScraper(session=session)

    print(f"作品情報を取得中... (ncode={args.ncode})")
    try:
        info = api.get_novel_info(args.ncode)
    except (NarouAPIError, requests.RequestException) as exc:
        print(f"[エラー] 作品情報の取得に失敗しました: {exc}", file=sys.stderr)
        return 1
    if cache:
        cache.save_info(info)

    print(f"タイトル: {info.title}")
    print(f"作者: {info.writer}")
    print(f"話数: {info.episode_count}話 ({'短編' if info.is_tanpen else '連載'})")

    # 章立て表示、または改稿検知(鮮度チェック)のために目次が必要かどうか
    need_toc = not info.is_tanpen and (
        not args.no_chapters or (cache is not None and not args.no_update_check)
    )
    toc: dict[int, TocEntry] = {}
    chapter_map: dict[int, str] = {}
    if need_toc:
        print("目次を取得中...")
        try:
            toc = fetch_with_retry(
                "目次", lambda: scraper.fetch_toc(args.ncode, info.general_all_no)
            )
        except (requests.RequestException, ScrapeError) as exc:
            print(f"  [警告] 目次の取得に失敗しました: {exc}", file=sys.stderr)
            toc = {}
        if not args.no_chapters:
            chapter_map = {i: e.chapter_title for i, e in toc.items() if e.chapter_title}
            if chapter_map:
                n_chapters = len(set(chapter_map.values()))
                print(f"  {n_chapters}章を検出しました。")

    if info.is_tanpen:
        episode_numbers: list[int | None] = [None]
    else:
        end = args.end or info.general_all_no
        episode_numbers = list(range(args.start, end + 1))

    episodes: list[Episode] = []
    total = len(episode_numbers)
    cache_hits = 0
    for i, ep_no in enumerate(episode_numbers, start=1):
        label = f"{ep_no}話" if ep_no else "(短編)"

        cached_episode = _load_from_cache_if_fresh(cache, args, toc, ep_no)
        if cached_episode is not None:
            print(f"  [{i}/{total}] {label} をキャッシュから読み込みました。")
            episodes.append(cached_episode)
            cache_hits += 1
            continue

        print(f"  [{i}/{total}] {label} を取得中...")
        try:
            episode = fetch_with_retry(label, lambda: scraper.fetch_episode(args.ncode, ep_no))
        except (requests.RequestException, ScrapeError) as exc:
            print(f"[エラー] {label} の取得に失敗しました: {exc}", file=sys.stderr)
            return 1
        episodes.append(episode)
        if cache:
            updated_at = toc[ep_no].updated_at if ep_no in toc else None
            cache.save_episode(episode, updated_at=updated_at)
        if i < total:
            polite_sleep(args.sleep)

    if cache_hits:
        print(f"({cache_hits}/{total}話をキャッシュから読み込みました)")

    if args.output:
        output_path = args.output
        if Path(output_path).suffix.lower() != ".epub":
            output_path += ".epub"
    else:
        output_path = f"{sanitize_filename(info.title)}.epub"
    print(f"EPUBを生成中... ({'横書き' if args.yoko else '縦書き'}) -> {output_path}")

    if args.backend == "aozoraepub3":
        work_dir = Path(output_path).resolve().parent
        work_dir.mkdir(parents=True, exist_ok=True)

        image_registry: dict[str, str] = {}
        if not args.no_images:
            print("  挿絵をダウンロード中(AozoraEpub3向けにファイル保存)...")
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
            print(f"[エラー] AozoraEpub3でのEPUB化に失敗しました: {exc}", file=sys.stderr)
            return 1

        if result.epub_path != Path(output_path):
            result.epub_path.replace(output_path)
        if result.warnings:
            print("  [警告] AozoraEpub3からの警告:")
            for w in result.warnings:
                print(f"    {w}")
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
            image_registry: dict[str, str] = {}
            if args.emit_aozora_txt_images:
                txt_work_dir = Path(output_path).resolve().parent
                print("  挿絵をダウンロード中(青空文庫記法テキスト向けにファイル保存)...")
                image_registry = download_images_for_aozora(
                    episodes, txt_work_dir, session=session, disk_cache=cache,
                )

            novel_text = build_novel_text(
                info.title, info.writer, info.story, episodes, chapter_map, image_registry,
            )
            txt_path = Path(output_path).with_suffix(".txt")
            txt_path.write_text(novel_text, encoding="utf-8")
            print(f"  青空文庫記法テキストを書き出しました -> {txt_path}")

    print("完了しました。")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
