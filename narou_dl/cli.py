"""コマンドラインエントリポイント

使い方 (インストール後):
    narou-dl N9669BK
    narou-dl N9669BK -o musyoku.epub --sleep 1.5
    narou-dl N9669BK --start 1 --end 20   # 一部の話だけ取得
    narou-dl N9669BK --yoko               # 横書きで生成(既定は縦書き)
    narou-dl N9669BK --no-chapters        # 章立てを無視してフラットな目次にする
    narou-dl N9669BK --no-images          # 挿絵を埋め込まない

または pip install せずに直接実行:
    python -m narou_dl N9669BK
"""
from __future__ import annotations

import argparse
import re
import sys
import time

import requests

from .api import NarouAPI, NarouAPIError, polite_sleep
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
    args = parser.parse_args(argv)

    session = requests.Session()
    api = NarouAPI(session=session)
    scraper = EpisodeScraper(session=session)

    print(f"作品情報を取得中... (ncode={args.ncode})")
    try:
        info = api.get_novel_info(args.ncode)
    except (NarouAPIError, requests.RequestException) as exc:
        print(f"[エラー] 作品情報の取得に失敗しました: {exc}", file=sys.stderr)
        return 1

    print(f"タイトル: {info.title}")
    print(f"作者: {info.writer}")
    print(f"話数: {info.episode_count}話 ({'短編' if info.is_tanpen else '連載'})")

    chapter_map: dict[int, str] = {}
    if not info.is_tanpen and not args.no_chapters:
        print("目次(章立て)を取得中...")
        try:
            chapter_map = fetch_with_retry(
                "目次", lambda: scraper.fetch_chapter_map(args.ncode, info.general_all_no)
            )
        except (requests.RequestException, ScrapeError) as exc:
            print(f"  [警告] 目次の取得に失敗したため、章立てなしで続行します: {exc}", file=sys.stderr)
            chapter_map = {}
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
    for i, ep_no in enumerate(episode_numbers, start=1):
        label = f"{ep_no}話" if ep_no else "(短編)"
        print(f"  [{i}/{total}] {label} を取得中...")
        try:
            episode = fetch_with_retry(label, lambda: scraper.fetch_episode(args.ncode, ep_no))
        except (requests.RequestException, ScrapeError) as exc:
            print(f"[エラー] {label} の取得に失敗しました: {exc}", file=sys.stderr)
            return 1
        episodes.append(episode)
        if i < total:
            polite_sleep(args.sleep)

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
    )
    print("完了しました。")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
