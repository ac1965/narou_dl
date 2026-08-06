"""取得済みの話データからEPUBを生成する(既定: 縦書き / オプション: 横書き)。

章立て(chapter_map)を渡すと、章の切り替わりに区切りページを挿入し、
目次(EPUB nav)も章でネストした構造にする。
本文中の <ruby> タグ(ルビ)や <img>(挿絵)はそのままEPUBへ埋め込まれる。
挿絵は参照URLから実際の画像データをダウンロードし、EPUB内部に同梱する。

追加時期::

    v1.0.0  縦書き/横書き切り替え(vertical引数)を含む基本のEPUB生成機能
    v1.1.0  chapter_map引数による章立て対応、ルビ・挿絵の埋め込み対応、
            disk_cache引数によるキャッシュ連携(挿絵の再ダウンロード回避)を追加
"""
from __future__ import annotations

import mimetypes
import re
import sys
from html import escape
from typing import TYPE_CHECKING

import requests
from ebooklib import epub

from .api import USER_AGENT, NovelInfo
from .scraper import Episode

if TYPE_CHECKING:
    from .cache import Cache

FONT_FAMILY = (
    '"Hiragino Mincho ProN", "Hiragino Mincho Pro", "Yu Mincho", '
    '"YuMincho", "Noto Serif CJK JP", "Noto Serif JP", "IPAMincho", serif'
)

_DIGIT_RUN_RE = re.compile(r"[0-9]{1,4}")
_TAG_SPLIT_RE = re.compile(r"(<[^>]+>)")
_IMG_TAG_RE = re.compile(r'<img src="([^"]*)" alt="([^"]*)"/>')


def _build_css(vertical: bool) -> str:
    if vertical:
        writing_mode_block = """
  writing-mode: vertical-rl;
  -epub-writing-mode: vertical-rl;
  -webkit-writing-mode: vertical-rl;
  text-orientation: mixed;
  -webkit-text-orientation: mixed;
  -epub-text-orientation: mixed;
  line-break: strict;
  word-break: normal;
  height: 100%;"""
    else:
        writing_mode_block = """
  writing-mode: horizontal-tb;
  -epub-writing-mode: horizontal-tb;
  -webkit-writing-mode: horizontal-tb;
  line-break: strict;
  word-break: normal;"""

    tcy_rule = (
        """
.tcy {
  -webkit-text-combine: horizontal;
  -epub-text-combine: horizontal;
  text-combine: horizontal;
  text-combine-upright: all;
}
"""
        if vertical
        else ""
    )

    return f"""
@charset "UTF-8";
html, body {{{writing_mode_block}
  font-family: {FONT_FAMILY};
  font-size: 1em;
  line-height: 1.9;
  margin: 0;
  padding: 0;
}}
h1 {{
  font-size: 1.3em;
  line-height: 1.9;
  margin: 0 0 1.2em 0;
  font-family: {FONT_FAMILY};
}}
h1.chapter-divider {{
  font-size: 1.6em;
}}
p {{
  margin: 0;
  text-indent: {"0" if vertical else "1em"};
}}
rt {{
  font-size: 0.5em;
}}
img {{
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1em auto;
}}
{tcy_rule}"""


def _combine_digits(text: str, vertical: bool, already_html: bool = False) -> str:
    """半角数字の連続(1〜4桁)を縦中横(tcy)化する(横書き時は無効)

    already_html=True の場合、text は既にHTMLエスケープ・タグ付与済みの
    安全な文字列として扱う(escape() を重ねて適用しない)。
    タグの内側(属性値など)の数字は巻き込まないよう、タグの外側の
    テキスト部分にのみ縦中横化を適用する。
    """
    raw = text if already_html else escape(text)
    if not vertical:
        return raw

    def _wrap(s: str) -> str:
        return _DIGIT_RUN_RE.sub(lambda m: f'<span class="tcy">{m.group()}</span>', s)

    parts = _TAG_SPLIT_RE.split(raw)
    # split結果は [テキスト, タグ, テキスト, タグ, ...] という偶数indexがテキスト
    for i in range(0, len(parts), 2):
        parts[i] = _wrap(parts[i])
    return "".join(parts)


def _paragraphs_to_flow_html(paragraphs: list[str], vertical: bool, already_html: bool = False) -> str:
    """段落配列を、章全体で1つの流し込みHTML(<br/>区切り)にする"""
    lines = []
    for text in paragraphs:
        stripped = text.strip("\u3000 \t\r\n")
        if not stripped:
            lines.append("")
        else:
            lines.append(_combine_digits(text, vertical, already_html=already_html))
    return "<br/>\n".join(lines)


class _ImageEmbedder:
    """本文HTML中の<img src="URL">を実データに差し替えてEPUBへ同梱するヘルパー

    disk_cache を渡すと、ダウンロード済みの画像データをディスクに保存し、
    次回以降の実行では再ダウンロードせずに済ませる。
    """

    def __init__(
        self,
        book: epub.EpubBook,
        session: requests.Session | None,
        enabled: bool,
        disk_cache: "Cache | None" = None,
    ):
        self.book = book
        self.session = session or requests.Session()
        if session is None:
            self.session.headers["User-Agent"] = USER_AGENT
        self.enabled = enabled
        self.disk_cache = disk_cache
        self._resolved: dict[str, str] = {}  # 元URL -> EPUB内の相対パス("" は失敗)
        self._count = 0

    def process(self, html: str) -> str:
        if "<img " not in html:
            return html
        return _IMG_TAG_RE.sub(self._replace, html)

    def _replace(self, m: re.Match) -> str:
        if not self.enabled:
            return ""  # 挿絵埋め込み無効時はタグごと削除する

        url, alt = m.group(1), m.group(2)
        local_path = self._resolved.get(url)
        if local_path is None:
            local_path = self._obtain(url)
            self._resolved[url] = local_path
        if not local_path:
            return ""  # ダウンロード失敗時はタグごと削除する
        return f'<img src="{local_path}" alt="{alt}"/>'

    def _obtain(self, url: str) -> str:
        content: bytes
        content_type: str

        cached = self.disk_cache.load_image(url) if self.disk_cache else None
        if cached is not None:
            content, content_type = cached
        else:
            downloaded = self._download(url)
            if downloaded is None:
                return ""
            content, content_type = downloaded
            if self.disk_cache:
                self.disk_cache.save_image(url, content, content_type)

        return self._add_to_book(content, content_type)

    def _add_to_book(self, content: bytes, content_type: str) -> str:
        ext = mimetypes.guess_extension(content_type) or ".jpg"
        if ext == ".jpe":
            ext = ".jpg"
        self._count += 1
        file_name = f"images/illust_{self._count:04d}{ext}"
        image_item = epub.EpubImage(
            uid=f"img{self._count}",
            file_name=file_name,
            media_type=content_type,
            content=content,
        )
        self.book.add_item(image_item)
        return file_name

    def _download(self, url: str) -> tuple[bytes, str] | None:
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            return resp.content, content_type
        except (requests.RequestException, OSError) as exc:
            print(f"  [警告] 挿絵のダウンロードに失敗しました ({url}): {exc}", file=sys.stderr)
            return None


def build_epub(
    info: NovelInfo,
    episodes: list[Episode],
    output_path: str,
    vertical: bool = True,
    chapter_map: dict[int, str] | None = None,
    embed_images: bool = True,
    session: requests.Session | None = None,
    disk_cache: "Cache | None" = None,
) -> None:
    """1冊のEPUBファイルを書き出す

    Args:
        info: 作品メタデータ。
        episodes: 話データのリスト(index順にソート済みであること)。
        output_path: 出力先の.epubファイルパス。
        vertical: True(既定)なら縦書き、False なら横書きで生成する。
        chapter_map: 話数(1始まり) -> 章タイトル の対応表。
            指定すると章の切り替わりに区切りページを挿入し、
            目次(EPUB nav)を章でネストした構造にする。未指定/空ならフラットな目次。
        embed_images: True(既定)なら本文中の挿絵をダウンロードしてEPUBへ同梱する。
            False の場合、挿絵は本文から取り除かれる。
        session: 挿絵ダウンロードに使う requests.Session(省略時は新規作成)。
        disk_cache: cache.Cache インスタンス。指定するとダウンロード済みの
            挿絵データをディスクに保存し、次回以降は再ダウンロードしない。

    Returns:
        None。生成したEPUBは output_path に書き出される。
    """
    chapter_map = chapter_map or {}

    book = epub.EpubBook()
    book.set_identifier(f"narou-{info.ncode}")
    book.set_title(info.title)
    book.set_language("ja")
    book.add_author(info.writer)

    direction = "rtl" if vertical else "ltr"
    book.set_direction(direction)

    css_item = epub.EpubItem(
        uid="main_style",
        file_name="style/main.css",
        media_type="text/css",
        content=_build_css(vertical),
    )
    book.add_item(css_item)

    images = _ImageEmbedder(book, session, embed_images, disk_cache=disk_cache)

    spine_items: list = ["nav"]
    toc_entries: list = []
    current_section_chapters: list | None = None
    last_chapter_title: str | None = None

    def _add_intro() -> None:
        intro = epub.EpubHtml(
            title="あらすじ",
            file_name="intro.xhtml",
            lang="ja",
            content=(
                f"<h1>{escape(info.title)}</h1>"
                f"<p>作者: {escape(info.writer)}</p>"
                f"<p>{_paragraphs_to_flow_html(info.story.splitlines(), vertical)}</p>"
            ),
            direction=direction,
        )
        intro.add_item(css_item)
        book.add_item(intro)
        spine_items.append(intro)
        toc_entries.append(intro)

    _add_intro()

    for ep in episodes:
        chapter_title = chapter_map.get(ep.index)

        if chapter_title and chapter_title != last_chapter_title:
            divider = epub.EpubHtml(
                title=chapter_title,
                file_name=f"chapter_{ep.index:04d}.xhtml",
                lang="ja",
                content=f'<h1 class="chapter-divider">{escape(chapter_title)}</h1>',
                direction=direction,
            )
            divider.add_item(css_item)
            book.add_item(divider)
            spine_items.append(divider)

            current_section_chapters = [divider]
            toc_entries.append((epub.Section(chapter_title), current_section_chapters))
            last_chapter_title = chapter_title

        file_name = f"episode_{ep.index:04d}.xhtml"
        body_html = images.process(_paragraphs_to_flow_html(ep.paragraphs, vertical, already_html=True))
        content = f"<h1>{_combine_digits(ep.subtitle, vertical)}</h1>\n<p>{body_html}</p>"
        chapter = epub.EpubHtml(
            title=ep.subtitle or f"第{ep.index}話",
            file_name=file_name,
            lang="ja",
            content=content,
            direction=direction,
        )
        chapter.add_item(css_item)
        book.add_item(chapter)
        spine_items.append(chapter)

        if chapter_title and current_section_chapters is not None:
            current_section_chapters.append(chapter)
        else:
            toc_entries.append(chapter)

    book.toc = tuple(toc_entries)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    book.spine = spine_items

    epub.write_epub(output_path, book)
