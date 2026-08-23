"""コマンドラインエントリポイント

使い方 (インストール後)::

    narou-dl N9669BK
    narou-dl https://ncode.syosetu.com/n9669bk/  # ncodeの代わりに作品URLも指定できる
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

    narou-dl N9669BK --reveal              # 生成後、Finderでファイルを選択状態にする(macOSのみ)

    narou-dl N9669BK --library-add        # ダウンロード後、今回のオプションと共に
                                           # ライブラリに登録する
    narou-dl --update-all                 # ライブラリの全作品を登録時のオプションで
                                           # まとめて再取得する(ncode不要)
    narou-dl --library-list               # 登録済みの全作品を一覧表示する(ncode不要)
    narou-dl N9669BK --library-remove     # ライブラリから削除する(ダウンロードは行わない)

既定では取得した本文・章立て・挿絵はキャッシュディレクトリに保存され、
同じ作品を再度ダウンロードする際はキャッシュから読み込んでネットワークアクセスを省略する。
キャッシュディレクトリは --cache-dir > 環境変数 XDG_CACHE_HOME > ~/.cache/narou-dl
の優先順位で決まる(詳細は cache.py を参照。CLI/GUI/.appバンドルで共通)。

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
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from .api import NarouAPI, NarouAPIError, polite_sleep
from .aozora import build_novel_text
from .aozoraepub3_backend import AozoraEpub3Error, build_epub_via_aozoraepub3
from .cache import Cache
from .config import CONFIG_KEYS, config_path, load_config, save_config
from .epub_builder import build_epub
from .image_fetch import download_images_for_aozora
from .library import LIBRARY_OPTION_KEYS, Library
from .scraper import Episode, EpisodeScraper, ScrapeError, TocEntry

MAX_RETRIES = 3


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def extract_ncode(value: str) -> str:
    """ncode入力欄への入力値から、素のncode部分を取り出す

    以下のいずれの形式でも受け付ける(GUI・CLI共通で使う)::

        N9669BK
        n9669bk
        https://ncode.syosetu.com/n9669bk/
        https://ncode.syosetu.com/n9669bk/1/   (話ページのURLでも先頭部分を使う)
        ncode.syosetu.com/n9669bk              (スキーム省略)

    Args:
        value: ユーザーが入力したncode、またはなろうの作品/話ページURL。

    Returns:
        抽出したncode。URLと判断できない入力はそのまま返す
        (素のncode入力時に余計な変換をしないため)。ncode自体の妥当性は
        検証しない(誤りがあれば後続のAPI呼び出しで自然にエラーになる)。
    """
    value = value.strip()
    if "syosetu.com" not in value.lower() and "://" not in value:
        return value

    parsed_value = value if "://" in value else f"https://{value}"
    segments = [s for s in urlparse(parsed_value).path.split("/") if s]
    return segments[0] if segments else value


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
    saved = load_config()

    parser = argparse.ArgumentParser(
        description="小説家になろうの作品をダウンロードしてEPUBに変換する"
    )
    parser.add_argument(
        "ncode",
        nargs="?",
        default=None,
        help=(
            "作品コード (例: N9669BK)、または作品URL "
            "(例: https://ncode.syosetu.com/n9669bk/)。"
            "--update-all/--library-list指定時は省略可"
        ),
    )
    parser.add_argument(
        "-o", "--output",
        help="出力ファイル名 (省略時は作品タイトルから自動生成。.epub拡張子が無ければ自動付与する)",
    )
    parser.add_argument(
        "--sleep", type=float, default=saved["sleep"],
        help=f"各話取得後の待機秒数 (既定: {saved['sleep']}秒、サーバー負荷軽減のため)",
    )
    parser.add_argument("--start", type=int, default=1, help="開始話数 (既定: 1)")
    parser.add_argument("--end", type=int, default=None, help="終了話数 (既定: 最終話)")
    # yoko/no-chapters/no-images/emit-aozora-txt*は既定値をconfig.jsonから
    # 復元する。既定値がTrueの場合でも明示的に無効化できるよう、
    # BooleanOptionalAction(--no-yoko等の否定形を自動生成)を使う。
    # store_trueのままだと「常に真」な既定値を1回だけ偽に戻す手段が無く、
    # --save-configで書き戻すたびに他方(GUI等)が設定した値を意図せず
    # falseへ上書きしてしまうため。
    parser.add_argument(
        "--yoko", action=argparse.BooleanOptionalAction, default=saved["yoko"],
        help="横書きで生成する (既定は縦書き)",
    )
    # --no-chapters/--no-imagesはargparse.BooleanOptionalActionと組み合わせられない。
    # BooleanOptionalActionは「渡されたオプション文字列が"--no-"で始まるか否か」だけで
    # 真偽を決めるため、フラグ名自体が既に"--no-"で始まっていると、
    # 本来のフラグ(--no-chapters)も自動生成される否定形(--no-no-chapters)も
    # 両方Falseと判定されてしまい、Trueにする手段が無くなる(実機検証で確認)。
    # そのため同じdestを共有する2つのaction(store_true/store_false)に分け、
    # 上書き用の方はdefault=argparse.SUPPRESSにして既定値の登録を委ねる。
    parser.add_argument(
        "--no-chapters",
        dest="no_chapters",
        action="store_true",
        default=saved["no_chapters"],
        help=f"章立て(「第一章」などの区切り)を取得せず、フラットな目次にする (既定: {saved['no_chapters']})",
    )
    parser.add_argument(
        "--chapters",
        dest="no_chapters",
        action="store_false",
        default=argparse.SUPPRESS,
        help="--no-chaptersが既定で有効になっている場合、今回だけ章立てを取得する",
    )
    parser.add_argument(
        "--no-images",
        dest="no_images",
        action="store_true",
        default=saved["no_images"],
        help=f"本文中の挿絵をダウンロード・埋め込みせず、取り除く (既定: {saved['no_images']})",
    )
    parser.add_argument(
        "--images",
        dest="no_images",
        action="store_false",
        default=argparse.SUPPRESS,
        help="--no-imagesが既定で有効になっている場合、今回だけ挿絵を埋め込む",
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
            "いれば $XDG_CACHE_HOME/narou-dl、未設定なら ~/.cache/narou-dl)"
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
        default=saved["backend"],
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
        default=(
            Path(os.environ["AOZORAEPUB3_JAR"]) if os.environ.get("AOZORAEPUB3_JAR")
            else (Path(saved["aozoraepub3_jar"]) if saved["aozoraepub3_jar"] else None)
        ),
        help=(
            "--backend aozoraepub3 使用時に必須。AozoraEpub3(改造版)の.jarへのパス。"
            "未指定時は環境変数 AOZORAEPUB3_JAR、それも無ければ設定ファイルの値を使う"
        ),
    )
    parser.add_argument(
        "--device",
        default=saved["device"],
        help="--backend aozoraepub3 使用時のみ有効。AozoraEpub3のデバイス最適化オプション(例: kindle)",
    )
    parser.add_argument(
        "--emit-aozora-txt",
        action=argparse.BooleanOptionalAction,
        default=saved["emit_aozora_txt"],
        help=(
            "EPUB(ebooklibバックエンド)生成に加え、同じ話データから青空文庫記法の"
            "テキストファイル(出力ファイル名の拡張子を.txtにしたもの)も書き出す。"
            "--backend aozoraepub3 とは併用不可(そちらは既定でtxtを生成するため)"
        ),
    )
    parser.add_argument(
        "--emit-aozora-txt-images",
        action=argparse.BooleanOptionalAction,
        default=saved["emit_aozora_txt_images"],
        help=(
            "--emit-aozora-txt 使用時、挿絵もダウンロードして青空文庫記法の"
            "挿絵注記を含める(既定では挿絵注記は含めない)"
        ),
    )
    parser.add_argument(
        "--emit-pdf",
        action=argparse.BooleanOptionalAction,
        default=saved["emit_pdf"],
        help=(
            "生成したEPUBをChromium(Playwright)で描画してPDFも書き出す"
            "(narou_dl/pdf_builder.py)。縦書き/横書き・判型はEPUB自身の"
            "CSSから自動判定される。"
            '要 pip install -e ".[pdf]" と python -m playwright install '
            "chromium (いずれもプロジェクトルートで実行)"
        ),
    )
    parser.add_argument(
        "--reveal",
        action="store_true",
        help=(
            "生成後、macOSのFinderでEPUBファイルを選択状態で表示する"
            "(Finder上からダブルクリックで開けるようにする。macOS以外では無視される)"
        ),
    )
    parser.add_argument(
        "--save-config",
        action="store_true",
        help=(
            "今回指定したオプション(縦横・バックエンド設定等)を既定値として"
            "設定ファイルに保存する(次回以降のnarou-dl実行・GUI起動時にも適用される。"
            "GUIとも共有される)。ncodeを省略すると保存のみ行いダウンロードはしない"
        ),
    )
    parser.add_argument(
        "--library-add",
        action="store_true",
        help=(
            "ダウンロード後、この作品を今回指定したオプションと共に"
            "ライブラリに登録する(--update-allで追跡・一括更新できるようになる)"
        ),
    )
    parser.add_argument(
        "--library-remove",
        action="store_true",
        help="この作品をライブラリから削除する(ダウンロードは行わない。ncode必須)",
    )
    parser.add_argument(
        "--library-list",
        action="store_true",
        help="ライブラリに登録済みの全作品を一覧表示する(ncode指定不要)",
    )
    parser.add_argument(
        "--update-all",
        action="store_true",
        help=(
            "ライブラリに登録済みの全作品を、登録時のオプションでまとめて"
            "再取得する(ncode指定不要。新規話・改稿された話のみキャッシュの"
            "鮮度判定により効率的に取得される)"
        ),
    )
    args = parser.parse_args(argv)
    if args.ncode:
        args.ncode = extract_ncode(args.ncode)

    cache_dir_for_library = Path(args.cache_dir) if args.cache_dir else None

    if args.library_list:
        return _library_list(cache_dir_for_library)
    if args.update_all:
        return _update_all(parser, cache_dir_for_library)
    if args.library_remove:
        if not args.ncode:
            parser.error("--library-remove にはncodeの指定が必要です")
        return _library_remove(args.ncode, cache_dir_for_library)
    if args.save_config and not args.ncode:
        _save_current_config(args)
        print(f"設定を保存しました -> {config_path()}")
        return 0
    if not args.ncode:
        parser.error(
            "ncode を指定してください"
            "(または --update-all / --library-list / --save-config)"
        )

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

    if args.save_config:
        _save_current_config(args)
        print(f"設定を保存しました -> {config_path()}")

    return _download_and_build(args)


def _save_current_config(args: argparse.Namespace) -> None:
    """--save-config: 現在指定したオプションを設定ファイルに書き戻す。

    CLI・GUIが同じ narou_dl.config を読み書きするため、ここで保存した
    値は次回のCLI実行時の既定値になるのはもちろん、GUI起動時にも
    (逆にGUI側で保存した値もCLI起動時に)反映される。
    """
    values = {key: getattr(args, key) for key in CONFIG_KEYS}
    values["aozoraepub3_jar"] = str(values["aozoraepub3_jar"]) if values["aozoraepub3_jar"] else None
    save_config(values)


def _library_list(cache_dir: Path | None) -> int:
    """--library-list: 登録済みの全作品を一覧表示する。"""
    entries = Library(cache_dir).load()
    if not entries:
        print("ライブラリに登録された作品はありません。")
        return 0
    for entry in sorted(entries.values(), key=lambda e: e.title):
        print(f"{entry.ncode}\t{entry.title}\t(登録日: {entry.added_at})")
    return 0


def _library_remove(ncode: str, cache_dir: Path | None) -> int:
    """--library-remove: 作品をライブラリから削除する。"""
    if Library(cache_dir).remove(ncode):
        print(f"ライブラリから削除しました: {ncode}")
        return 0
    print(f"ライブラリに登録されていません: {ncode}", file=sys.stderr)
    return 1


def _update_all(parser: argparse.ArgumentParser, cache_dir: Path | None) -> int:
    """--update-all: ライブラリに登録済みの全作品を、登録時のオプションで
    まとめて再取得する。1作品の失敗は他の作品の処理を止めない。
    """
    entries = Library(cache_dir).load()
    if not entries:
        print("ライブラリに登録された作品がありません。")
        return 0

    print(f"ライブラリの{len(entries)}作品を更新します。")
    failed_titles: list[str] = []
    for i, entry in enumerate(sorted(entries.values(), key=lambda e: e.title), start=1):
        print(f"\n[{i}/{len(entries)}] {entry.title} ({entry.ncode})")
        # 登録時に保存したオプションを、新規にparseしたNamespace(既定値込み)
        # へ上書きする形で復元する。--library-add等の管理系フラグは
        # 誤って再実行されないよう明示的にFalseへ戻す。
        item_args = parser.parse_args([entry.ncode])
        for key, value in entry.options.items():
            if hasattr(item_args, key):
                setattr(item_args, key, value)
        if item_args.aozoraepub3_jar:
            item_args.aozoraepub3_jar = Path(item_args.aozoraepub3_jar)
        # update_all呼び出し時に指定された --cache-dir を各作品にも適用する
        # (library.json自体もこのcache_dirから読み込んでいるため揃える)
        item_args.cache_dir = str(cache_dir) if cache_dir else None
        item_args.library_add = False
        item_args.library_remove = False
        item_args.library_list = False
        item_args.update_all = False
        item_args.clear_cache = False

        try:
            status = _download_and_build(item_args)
        except Exception as exc:  # noqa: BLE001 - 1作品の失敗で全体を止めない
            print(f"[エラー] {entry.title} の更新に失敗しました: {exc}", file=sys.stderr)
            status = 1
        if status != 0:
            failed_titles.append(entry.title)

    if failed_titles:
        print(
            f"\n{len(failed_titles)}作品の更新に失敗しました: {', '.join(failed_titles)}",
            file=sys.stderr,
        )
        return 1
    print(f"\n{len(entries)}作品すべて更新しました。")
    return 0


def _download_and_build(args: argparse.Namespace) -> int:
    """1作品分のダウンロード〜EPUB(または青空文庫記法テキスト)生成を行う。

    run()から通常のCLI実行時に、_update_all()からライブラリ一括更新時に、
    それぞれ呼び出される共通の実処理本体。
    """
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

    if args.emit_pdf:
        try:
            from .pdf_builder import PdfEngineError, build_pdf
        except ImportError:
            print(
                "[エラー] --emit-pdf にはplaywrightが必要です。"
                'プロジェクトルートで pip install -e ".[pdf]" を'
                "実行してインストールしてください",
                file=sys.stderr,
            )
            return 1
        pdf_path = Path(output_path).with_suffix(".pdf")
        print(f"  PDFを生成中(Chromium)... -> {pdf_path}")
        try:
            build_pdf(output_path, pdf_path)
        except PdfEngineError as exc:
            print(
                f"[エラー] PDF生成に失敗しました: {exc}\n"
                "Chromium本体が未インストールの場合は "
                "python -m playwright install chromium を実行してください",
                file=sys.stderr,
            )
            return 1

    if args.library_add:
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        options = {key: getattr(args, key) for key in LIBRARY_OPTION_KEYS}
        options["aozoraepub3_jar"] = (
            str(options["aozoraepub3_jar"]) if options["aozoraepub3_jar"] else None
        )
        Library(cache_dir).add(args.ncode, info.title, options)
        print(f"ライブラリに登録しました: {info.title} ({args.ncode})")

    if args.reveal and sys.platform == "darwin":
        subprocess.run(["open", "-R", output_path], check=False)

    print("完了しました。")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
