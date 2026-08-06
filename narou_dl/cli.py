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

既定では取得した本文・章立て・挿絵はキャッシュディレクトリに保存され、
同じ作品を再度ダウンロードする際はキャッシュから読み込んでネットワークアクセスを省略する。
キャッシュディレクトリは --cache-dir > 環境変数 XDG_CACHE_HOME > カレントディレクトリの
./.narou-dl-cache の優先順位で決まる(詳細は cache.py を参照)。

または pip install せずに直接実行::

    python -m narou_dl N9669BK

追加時期::

    v1.0.0  基本のCLI(ncode指定、-o/--sleep/--start/--end/--yoko)
    v1.1.0  --no-chapters/--no-images/--refresh/--no-cache/--clear-cache/--cache-dir
            オプションを追加(章立て・ルビ・挿絵・ローカルキャッシュ対応に伴う)
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import requests

from .api import NarouAPI, NarouAPIError, polite_sleep
from .cache import Cache
from .epub_builder import build_epub
from .scraper import Episode, EpisodeScraper, ScrapeError

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
    parser.add_argument("-o", "--output", help="出力ファイル名 (省略時は作品タイトルから自動生成)")
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
    args = parser.parse_args(argv)

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

    chapter_map: dict[int, str] = {}
    if not info.is_tanpen and not args.no_chapters:
        cached_chapter_map = None if (cache is None or args.refresh) else cache.load_chapter_map(
            info.general_all_no
        )
        if cached_chapter_map is not None:
            chapter_map = cached_chapter_map
            print("目次(章立て)をキャッシュから読み込みました。")
        else:
            print("目次(章立て)を取得中...")
            try:
                chapter_map = fetch_with_retry(
                    "目次", lambda: scraper.fetch_chapter_map(args.ncode, info.general_all_no)
                )
            except (requests.RequestException, ScrapeError) as exc:
                print(f"  [警告] 目次の取得に失敗したため、章立てなしで続行します: {exc}", file=sys.stderr)
                chapter_map = {}
            if cache and chapter_map:
                cache.save_chapter_map(chapter_map, info.general_all_no)
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

        cached_episode = (
            None if (cache is None or args.refresh or ep_no is None) else cache.load_episode(ep_no)
        )
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
            cache.save_episode(episode)
        if i < total:
            polite_sleep(args.sleep)

    if cache_hits:
        print(f"({cache_hits}/{total}話をキャッシュから読み込みました)")

    output_path = args.output or f"{sanitize_filename(info.title)}.epub"
    print(f"EPUBを生成中... ({'横書き' if args.yoko else '縦書き'}) -> {output_path}")
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
    print("完了しました。")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
