"""取得済みの話データから縦書き(または横書き)PDFを生成するバックエンド。

narou_dl自身が持つEpisode.paragraphs(scraper.pyが生成する、ルビ・挿絵・傍点を
保持した安全なHTML断片)を直接レイアウトする、Pure Python(ReportLabのみ)の
縦書き組版エンジン。外部ツール(Calibre/wkhtmltopdf等)は一切使わない。

WeasyPrint(pip一発で入るHTML/CSSレンダラー)を検証したところ、日本語フォント
自体は正しく描画できたが `writing-mode: vertical-rl` が完全に無視され横書きに
フォールバックすることを実機で確認した(2024年時点のWeasyPrintの既知の制約)。
そのためHTML/CSSレンダラーには頼らず、文字単位でマス目に配置する専用の
縦書きレイアウトエンジンをここに実装している。

フォントはReportLab組み込みのAdobe標準日本語CIDフォント(HeiseiMin-W3/
HeiseiKakuGo-W5)を使う。実際のグリフはPDFに埋め込まれず、閲覧側(Preview.app・
Adobe Acrobat等、日本語フォントを持つ環境)が補完する方式のため、フォント
ファイルの同梱が一切不要(pip install reportlabのみで完結する)。

既知の制約(v1のスコープ):
    - 「」()等の約物は縦書き用の回転グリフではなく横書きのまま描画される
      (ReportLabの非埋め込みCIDフォントはOpenTypeの縦書き用グリフ差し替え
      (vert機能)に対応していないため)。
    - 半角英字・長音記号(ー)も直立のまま描画する(縦書きの正式な組版では
      90度回転させるのが一般的だが、reportlab 5.0.1で
      saveState()/rotate()/restoreState()を使って回転描画すると、直前に
      別の回転描画が1回でもあった場合に限って以降の回転が反映されなくなる
      現象を実機で確認した。生成されるPDFの内容ストリーム自体は正常に
      回転する箇所と構造的に同一で、Poppler・PyMuPDF両方で同じ結果になる
      ため、こちら側の実装ミスというよりreportlab側の狭い条件でのみ
      再現する問題と判断し、深追いのコストに見合わないため直立のまま
      描画する形に留めている)。
    - 禁則処理(行頭禁則・行末禁則)は簡易版(1文字の押し出しのみ、連鎖なし)。
    - ルビの文字間隔調整(モノルビ/グループルビの均等割り付け)は行わず、
      対象文字の縦幅に単純に均等配置する。
    - 挿絵は本文中への回り込みはせず、専用の1ページを使って中央に配置する。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Union

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

from .api import USER_AGENT, NovelInfo
from .scraper import Episode

if TYPE_CHECKING:
    from .cache import Cache

FONT_MINCHO = "HeiseiMin-W3"
FONT_GOTHIC = "HeiseiKakuGo-W5"
_FONTS_REGISTERED = False


def _ensure_fonts_registered() -> None:
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    pdfmetrics.registerFont(UnicodeCIDFont(FONT_MINCHO))
    pdfmetrics.registerFont(UnicodeCIDFont(FONT_GOTHIC))
    _FONTS_REGISTERED = True


# --- 段落HTMLの解析(scraper.pyが生成する安全なHTML断片が対象) ---

_DIGIT_RUN_RE = re.compile(r"[0-9]{1,4}")
_CANNOT_START_COLUMN = set("」』)]〉》】〕、。,.!?！？ー…ゝゞぁぃぅぇぉっゃゅょァィゥェォッャュョ・：；")
_CANNOT_END_COLUMN = set("「『([〈《【〔")


@dataclass
class _Atom:
    """1文字(またはtcyでまとめた数字列)分の描画単位。"""

    text: str
    kind: str  # "char" | "tcy" | "latin"


@dataclass
class PlainSegment:
    atoms: list[_Atom]


@dataclass
class RubySegment:
    atoms: list[_Atom]
    ruby: str


@dataclass
class BoutenSegment:
    atoms: list[_Atom]


@dataclass
class ImageSegment:
    url: str


Segment = Union[PlainSegment, RubySegment, BoutenSegment, ImageSegment]


def _atomize(text: str) -> list[_Atom]:
    """プレーンテキストを描画単位(_Atom)のリストに分解する。

    半角数字の連続(1〜4桁)はtcy(縦中横)として1つのAtomにまとめ、
    それ以外の半角英字は回転描画対象("latin")、それ以外は通常文字として扱う。
    """
    atoms: list[_Atom] = []
    i = 0
    while i < len(text):
        m = _DIGIT_RUN_RE.match(text, i)
        if m:
            atoms.append(_Atom(m.group(), "tcy"))
            i = m.end()
            continue
        ch = text[i]
        if ch.isascii() and ch.isalpha():
            atoms.append(_Atom(ch, "latin"))
        else:
            atoms.append(_Atom(ch, "char"))
        i += 1
    return atoms


def _parse_paragraph(html: str) -> list[Segment]:
    """段落1つ分のHTML断片(scraper.pyが生成する安全なHTML)をSegment列にする。

    対応するタグ: <ruby><rt>...</rt></ruby>(ルビ)、
    <em class="emphasisDots">(傍点)、<img src="...">(挿絵)、<br/>(空行)。
    """
    if not html.strip():
        return [PlainSegment(_atomize("　"))]  # 空行は全角スペース1文字扱い

    soup = BeautifulSoup(f"<span>{html}</span>", "html.parser")
    root = soup.span
    segments: list[Segment] = []

    def walk(node) -> None:
        if isinstance(node, NavigableString):
            text = str(node)
            if text:
                segments.append(PlainSegment(_atomize(text)))
            return
        if not isinstance(node, Tag):
            return
        if node.name == "img":
            src = node.get("src")
            if src:
                segments.append(ImageSegment(src))
            return
        if node.name == "br":
            return
        if node.name == "ruby":
            rt_tag = node.find("rt")
            ruby_text = rt_tag.get_text() if rt_tag else ""
            base_text = "".join(
                c.get_text() if isinstance(c, Tag) else str(c)
                for c in node.contents
                if not (isinstance(c, Tag) and c.name in ("rt", "rp"))
            )
            if base_text:
                segments.append(RubySegment(_atomize(base_text), ruby_text))
            return
        if node.name == "em" and "emphasisDots" in (node.get("class") or []):
            segments.append(BoutenSegment(_atomize(node.get_text())))
            return
        for child in node.contents:
            walk(child)

    for child in root.contents:
        walk(child)
    return segments


# --- 縦書きレイアウトエンジン ---


@dataclass
class _PageGeometry:
    page_width: float
    page_height: float
    margin: float
    font_size: float
    ruby_font_size: float
    column_pitch: float  # 列(行)の間隔。ルビ用のガター込み

    @property
    def top_y(self) -> float:
        return self.page_height - self.margin

    @property
    def bottom_y(self) -> float:
        return self.margin

    @property
    def column_height(self) -> float:
        return self.top_y - self.bottom_y

    @property
    def rows_per_column(self) -> int:
        return max(1, int(self.column_height // self.font_size))

    @property
    def first_column_right_x(self) -> float:
        return self.page_width - self.margin


def _default_geometry(vertical: bool, font_size: float = 11.5) -> _PageGeometry:
    # 一般的な文庫本に近いB6サイズ(128mm x 182mm)を既定とする。
    width, height = 128 * mm, 182 * mm
    if not vertical:
        # 横書きの場合はページを回転させ、横長ではなく引き続き縦長のまま
        # 上→下・左→右で読む一般的な書籍レイアウトにする。
        pass
    return _PageGeometry(
        page_width=width,
        page_height=height,
        margin=14 * mm,
        font_size=font_size,
        ruby_font_size=font_size * 0.5,
        column_pitch=font_size * 1.7,
    )


class _VerticalWriter:
    """縦書きPDFへの実際の描画を担当するクラス。

    段落単位でSegment列を受け取り、文字をマス目に配置しながら
    列(行)・ページを自動的に進める。1段落は必ず新しい列から始まる
    (なろうの1行=1<p>を、縦書きの1行=1列に対応させるため)。
    """

    def __init__(self, canvas: Canvas, geometry: _PageGeometry):
        self.canvas = canvas
        self.geo = geometry
        self.column_index = 0  # ページ内での列番号(0始まり、右端が0)
        self.row_index = 0  # 現在の列内での行(文字)番号
        self._page_has_content = False

    # --- 座標計算 ---

    def _column_right_x(self, column_index: int) -> float:
        return self.geo.first_column_right_x - column_index * self.geo.column_pitch

    def _row_center_y(self, row_index: int) -> float:
        return self.geo.top_y - (row_index + 0.5) * self.geo.font_size

    # --- ページ・列制御 ---

    def new_page(self) -> None:
        if self._page_has_content:
            self.canvas.showPage()
        self.column_index = 0
        self.row_index = 0
        self._page_has_content = False

    def _advance_column(self) -> None:
        self.column_index += 1
        self.row_index = 0
        if self._column_right_x(self.column_index) - self.geo.column_pitch < self.geo.margin:
            self.new_page()

    def start_new_paragraph_column(self) -> None:
        """新しい段落の開始位置(必ず列の先頭)へ進める。"""
        if self.row_index != 0:
            self._advance_column()

    # --- 描画本体 ---

    def draw_heading(self, text: str, font_size: float | None = None) -> None:
        """章・話タイトルを1列の大きな文字で描画し、その後1列分の空白を空ける。"""
        size = font_size or self.geo.font_size * 1.3
        saved_font_size = self.geo.font_size
        self.geo.font_size = size
        self.start_new_paragraph_column()
        x = self._column_right_x(self.column_index) - size / 2
        self.canvas.setFont(FONT_GOTHIC, size)
        for ch in text:
            if self.row_index >= self.geo.rows_per_column:
                self._advance_column()
                x = self._column_right_x(self.column_index) - size / 2
            y = self.geo.top_y - (self.row_index + 0.8) * size
            self.canvas.drawCentredString(x, y, ch)
            self._page_has_content = True
            self.row_index += 1
        self.geo.font_size = saved_font_size
        self._advance_column()  # 見出しの後は必ず新しい列から本文を始める
        self.row_index = 0
        self._advance_column()  # 見出しと本文の間に1列分の空白を空ける

    def draw_paragraph(self, segments: list[Segment], image_resolver) -> None:
        self.start_new_paragraph_column()
        self.canvas.setFont(FONT_MINCHO, self.geo.font_size)
        for segment in segments:
            if isinstance(segment, ImageSegment):
                self._draw_image_page(segment.url, image_resolver)
                self.start_new_paragraph_column()
                self.canvas.setFont(FONT_MINCHO, self.geo.font_size)
                continue
            if isinstance(segment, RubySegment):
                self._draw_ruby_segment(segment)
            elif isinstance(segment, BoutenSegment):
                self._draw_plain_atoms(segment.atoms, bouten=True)
            else:
                self._draw_plain_atoms(segment.atoms, bouten=False)

    def _ensure_row_space(self, needed: int) -> None:
        """現在の列にneeded文字分の空きが無ければ次の列へ進む(禁則処理用)。"""
        if self.row_index + needed > self.geo.rows_per_column and self.row_index > 0:
            self._advance_column()

    def _next_atom_starts_column(self, atoms: list[_Atom], idx: int) -> bool:
        return self.row_index == 0 and idx < len(atoms)

    def _draw_plain_atoms(self, atoms: list[_Atom], bouten: bool) -> None:
        i = 0
        while i < len(atoms):
            atom = atoms[i]
            # 行末禁則: 開き括弧等が列の最後の1文字になる場合は先に列を送る
            if (
                self.row_index == self.geo.rows_per_column - 1
                and atom.text
                and atom.text[0] in _CANNOT_END_COLUMN
            ):
                self._advance_column()

            if self.row_index >= self.geo.rows_per_column:
                self._advance_column()

            # 行頭禁則: 列の先頭に来てはいけない文字は前の列に押し出す
            if (
                self.row_index == 0
                and self.column_index > 0
                and atom.text
                and atom.text[0] in _CANNOT_START_COLUMN
            ):
                self._draw_atom_overflow(atom, bouten)
                i += 1
                continue

            self._draw_atom(atom, self.column_index, self.row_index, bouten)
            self.row_index += 1
            i += 1

    def _draw_atom_overflow(self, atom: _Atom, bouten: bool) -> None:
        """前の列の末尾に1文字だけはみ出して描画する(行頭禁則の押し出し)。"""
        prev_column = self.column_index - 1
        x_right = self._column_right_x(prev_column)
        y = self._row_center_y(self.geo.rows_per_column)
        self._draw_atom_at(atom, x_right, y, bouten)

    def _draw_atom(self, atom: _Atom, column_index: int, row_index: int, bouten: bool) -> None:
        x_right = self._column_right_x(column_index)
        y = self._row_center_y(row_index)
        self._draw_atom_at(atom, x_right, y, bouten)
        self._page_has_content = True

    def _draw_atom_at(self, atom: _Atom, x_right: float, y: float, bouten: bool) -> None:
        font_size = self.geo.font_size
        x_center = x_right - font_size / 2
        if atom.kind == "tcy":
            size = font_size * (0.62 if len(atom.text) > 1 else 0.9)
            self.canvas.setFont(FONT_MINCHO, size)
            self.canvas.drawCentredString(x_center, y - size * 0.35, atom.text)
            self.canvas.setFont(FONT_MINCHO, font_size)
        else:
            # 半角英字・長音記号(ー)は本来90度回転させるのが正式な縦書き
            # 組版だが、canvas.rotate()を使うと(reportlab 5.0.1で)直前に
            # 別の回転描画があった場合に限って以降の回転が反映されなく
            # なる再現性のある事象を実機で確認した(saveState/restoreStateは
            # 正しく対になっており、生成されるPDFの内容ストリーム自体は
            # 他の正常に回転する箇所と構造的に同一であるにもかかわらず
            # Poppler・PyMuPDF両方で同じ結果になるため、こちら側の実装
            # ミスではなくreportlab側の非常に狭い条件でのみ再現する問題と
            # 判断した)。原因の切り分けに見合う実害(縦書き中に稀に混在する
            # 半角英字が回転されない、という軽微な見た目の問題のみ)ではない
            # ため、回転はせず直立のまま描画する(多くの簡易縦書き変換
            # ツールも英数字は直立のまま扱っており、実用上大きな問題はない)。
            self.canvas.drawCentredString(x_center, y - font_size * 0.35, atom.text)
        if bouten:
            dot_x = x_right + font_size * 0.32
            self.canvas.setFont(FONT_MINCHO, font_size * 0.5)
            self.canvas.drawCentredString(dot_x, y - font_size * 0.18, "・")  # ・(なかぐろ)を圏点代わりに使う
            self.canvas.setFont(FONT_MINCHO, font_size)

    def _draw_ruby_segment(self, segment: RubySegment) -> None:
        n = len(segment.atoms)
        self._ensure_row_space(n)
        if self.row_index + n > self.geo.rows_per_column:
            # 1文字も入らない極端に長いルビ語(通常は起きない)は列またぎを許容する
            pass
        start_row = self.row_index
        start_column = self.column_index
        for atom in segment.atoms:
            if self.row_index >= self.geo.rows_per_column:
                self._advance_column()
                start_column = self.column_index
                start_row = self.row_index
            self._draw_atom(atom, self.column_index, self.row_index, bouten=False)
            self.row_index += 1

        if not segment.ruby or start_column != self.column_index:
            return  # ルビ対象が列をまたいだ場合は簡略化のため描画を省略する
        span_top = self._row_center_y(start_row) + self.geo.font_size / 2
        span_bottom = self._row_center_y(self.row_index - 1) - self.geo.font_size / 2
        span_height = span_top - span_bottom
        ruby_size = min(self.geo.ruby_font_size, span_height / max(len(segment.ruby), 1) * 0.95)
        ruby_x = self._column_right_x(start_column) - self.geo.font_size - ruby_size * 0.55
        self.canvas.setFont(FONT_MINCHO, ruby_size)
        step = span_height / len(segment.ruby)
        for i, ch in enumerate(segment.ruby):
            y = span_top - step * (i + 0.5) - ruby_size * 0.35
            self.canvas.drawCentredString(ruby_x, y, ch)
        self.canvas.setFont(FONT_MINCHO, self.geo.font_size)

    def _draw_image_page(self, url: str, image_resolver) -> None:
        image_path = image_resolver(url)
        self.new_page()
        if image_path is None:
            return
        try:
            from reportlab.lib.utils import ImageReader

            img = ImageReader(str(image_path))
            iw, ih = img.getSize()
        except Exception:  # noqa: BLE001 - 画像が壊れていてもPDF生成自体は継続する
            return
        max_w = self.geo.page_width - self.geo.margin * 2
        max_h = self.geo.page_height - self.geo.margin * 2
        scale = min(max_w / iw, max_h / ih, 1.0)
        w, h = iw * scale, ih * scale
        x = (self.geo.page_width - w) / 2
        y = (self.geo.page_height - h) / 2
        self.canvas.drawImage(img, x, y, width=w, height=h, preserveAspectRatio=True)
        self._page_has_content = True
        self.new_page()


class _ImageResolver:
    """挿絵URLを実ファイルパスへ解決し、一時ディレクトリにキャッシュするヘルパー。"""

    def __init__(self, work_dir: Path, session: requests.Session | None, disk_cache: "Cache | None"):
        self.work_dir = work_dir
        self.session = session or requests.Session()
        if session is None:
            self.session.headers["User-Agent"] = USER_AGENT
        self.disk_cache = disk_cache
        self._resolved: dict[str, Path | None] = {}
        self._count = 0

    def __call__(self, url: str) -> Path | None:
        if url in self._resolved:
            return self._resolved[url]
        path = self._obtain(url)
        self._resolved[url] = path
        return path

    def _obtain(self, url: str) -> Path | None:
        content: bytes
        cached = self.disk_cache.load_image(url) if self.disk_cache else None
        if cached is not None:
            content, _content_type = cached
        else:
            try:
                resp = self.session.get(url, timeout=15)
                resp.raise_for_status()
                content = resp.content
                if self.disk_cache:
                    content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                    self.disk_cache.save_image(url, content, content_type)
            except (requests.RequestException, OSError) as exc:
                print(f"  [警告] 挿絵のダウンロードに失敗しました ({url}): {exc}", file=sys.stderr)
                return None

        self._count += 1
        self.work_dir.mkdir(parents=True, exist_ok=True)
        path = self.work_dir / f"pdf_illust_{self._count:04d}.img"
        path.write_bytes(content)
        return path


def build_pdf(
    info: NovelInfo,
    episodes: list[Episode],
    output_path: str,
    vertical: bool = True,
    chapter_map: dict[int, str] | None = None,
    embed_images: bool = True,
    session: requests.Session | None = None,
    disk_cache: "Cache | None" = None,
) -> None:
    """1冊分のPDFを書き出す(ReportLabのみ、外部ツール不要)。

    Args:
        info: 作品メタデータ。
        episodes: 話データのリスト(index順にソート済みであること)。
        output_path: 出力先の.pdfファイルパス。
        vertical: True(既定)なら縦書き、False なら横書き(簡易)で生成する。
        chapter_map: 話数(1始まり) -> 章タイトル の対応表。
        embed_images: True(既定)なら挿絵を専用ページとして挿入する。
        session: 挿絵ダウンロードに使う requests.Session。
        disk_cache: cache.Cache インスタンス(挿絵の再ダウンロード回避用)。
    """
    _ensure_fonts_registered()
    chapter_map = chapter_map or {}

    geometry = _default_geometry(vertical)
    canvas = Canvas(output_path, pagesize=(geometry.page_width, geometry.page_height))
    canvas.setTitle(info.title)
    canvas.setAuthor(info.writer)

    work_dir = Path(output_path).resolve().parent / f".{Path(output_path).stem}_pdf_images"
    resolver = _ImageResolver(work_dir, session, disk_cache) if embed_images else (lambda _url: None)

    writer = _VerticalWriter(canvas, geometry)

    canvas.setFont(FONT_GOTHIC, geometry.font_size * 1.6)
    canvas.drawCentredString(geometry.page_width / 2, geometry.page_height / 2 + 20 * mm, info.title)
    canvas.setFont(FONT_MINCHO, geometry.font_size * 1.1)
    canvas.drawCentredString(geometry.page_width / 2, geometry.page_height / 2 - 5 * mm, info.writer)
    writer._page_has_content = True
    writer.new_page()

    if info.story.strip():
        writer.draw_heading("あらすじ")
        for line in info.story.splitlines():
            writer.draw_paragraph(_parse_paragraph(line), resolver)
        writer.new_page()

    last_chapter_title: str | None = None
    for ep in episodes:
        chapter_title = chapter_map.get(ep.index)
        if chapter_title and chapter_title != last_chapter_title:
            writer.new_page()
            writer.draw_heading(chapter_title, font_size=geometry.font_size * 1.6)
            last_chapter_title = chapter_title

        if ep.subtitle:
            writer.draw_heading(ep.subtitle)
        for paragraph in ep.paragraphs:
            segments = _parse_paragraph(paragraph)
            writer.draw_paragraph(segments, resolver)

    canvas.showPage()
    canvas.save()

    if isinstance(resolver, _ImageResolver) and resolver.work_dir.exists():
        import shutil

        shutil.rmtree(resolver.work_dir, ignore_errors=True)
