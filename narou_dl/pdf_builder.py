"""生成済みEPUBをChromium(Playwright)で描画してPDFに変換する。

以前はReportLabで文字単位のマス目に配置する独自の縦書き組版エンジンを
実装していたが、半角英字・長音記号(ー)が正しく回転しない既知のバグを
解消できなかった。EPUBのXHTML/CSSをそのままChromiumに渡して描画させれば、
縦書き・ルビ・禁則処理はブラウザのネイティブ実装がすべて正しく処理して
くれるため、そちらに置き換えた。

設計方針:
    - EPUBのXHTML/CSSはできるだけそのまま利用する。ルビ・禁則処理は
      Chromiumのレイアウトエンジンに任せ、こちらでは実装しない。
    - 書字方向(縦書き/横書き)・判型・余白は、まずEPUB自身のCSS
      (`@page` / `html, body` の `writing-mode`)から検出し、検出でき
      なかった項目だけを引数の既定値で補う(EPUBのCSSを強制上書きしない)。
    - ページ番号はChromiumの `@page` マージンボックスに頼らず、生成後の
      PDFにReportLab+pypdfで別途重ね書きする。

要 `pip install -e ".[pdf]"` に加えて `python -m playwright install chromium`
(Chromium本体はpipのインストール対象に含まれないため別途必要)。
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from playwright.async_api import async_playwright


class PdfEngineError(RuntimeError):
    """Chromiumでの変換に失敗した場合に送出する(Chromium本体が未インストール、
    EPUBの構造が壊れている等)。playwright自体が未インストールの場合は
    このモジュールのimport自体が通常のImportErrorになる(呼び出し側で
    ハンドリングする)。"""


# ----------------------------------------------------------------------
# EPUB: OPF / manifest / spine
# ----------------------------------------------------------------------

def find_opf(epub_dir: Path) -> Path:
    container = epub_dir / "META-INF" / "container.xml"

    soup = BeautifulSoup(
        container.read_text(encoding="utf-8"),
        "xml",
    )

    rootfile = soup.find("rootfile")

    if rootfile is None:
        raise RuntimeError("EPUB container.xml に OPF がありません")

    full_path = rootfile.get("full-path")

    if not full_path:
        raise RuntimeError("OPF の full-path がありません")

    return epub_dir / full_path


@dataclass
class ManifestItem:
    path: Path
    media_type: str
    properties: str


@dataclass
class EpubDocument:
    opf: Path
    opf_soup: BeautifulSoup
    manifest: dict[str, ManifestItem]
    spine: list[Path]
    spine_ids: list[str]
    cover: Path | None
    toc_entries: list[dict]


def _resolve_href(base_dir: Path, href: str) -> Path:
    parsed = urlparse(href)

    return (base_dir / unquote(parsed.path)).resolve()


def parse_epub(epub_dir: Path) -> EpubDocument:
    opf = find_opf(epub_dir)
    opf_dir = opf.parent

    soup = BeautifulSoup(
        opf.read_text(encoding="utf-8"),
        "xml",
    )

    manifest: dict[str, ManifestItem] = {}

    for item in soup.find_all("item"):
        item_id = item.get("id")
        href = item.get("href")
        media_type = item.get("media-type") or ""

        if item_id and href:
            manifest[item_id] = ManifestItem(
                path=_resolve_href(opf_dir, href),
                media_type=media_type,
                properties=item.get("properties", "") or "",
            )

    spine_tag = soup.find("spine")

    if spine_tag is None:
        raise RuntimeError("EPUB に spine がありません")

    spine = []
    spine_ids = []

    for itemref in spine_tag.find_all("itemref"):
        idref = itemref.get("idref")

        if idref not in manifest:
            continue

        item = manifest[idref]

        if item.media_type in (
            "application/xhtml+xml",
            "text/html",
        ):
            spine.append(item.path)
            spine_ids.append(idref)

    if not spine:
        raise RuntimeError("EPUBのspineにXHTMLがありません")

    cover = find_cover_image(soup, manifest, opf_dir)

    toc_entries = find_toc_entries(
        soup,
        manifest,
        spine_tag,
    )

    return EpubDocument(
        opf=opf,
        opf_soup=soup,
        manifest=manifest,
        spine=spine,
        spine_ids=spine_ids,
        cover=cover,
        toc_entries=toc_entries,
    )


# ----------------------------------------------------------------------
# EPUB: cover image
# ----------------------------------------------------------------------

def find_cover_image(
    opf_soup: BeautifulSoup,
    manifest: dict[str, ManifestItem],
    opf_dir: Path,
) -> Path | None:
    """
    EPUBの表紙画像を解決する(EPUB2/EPUB3両対応)。
    """

    # EPUB3: manifest item with properties="cover-image"
    for item in manifest.values():
        if "cover-image" in item.properties.split():
            if item.path.exists():
                return item.path

    # EPUB2: <meta name="cover" content="some-manifest-id"/>
    meta_cover = opf_soup.find(
        "meta",
        attrs={"name": "cover"},
    )

    if meta_cover:
        content_id = meta_cover.get("content")

        if content_id in manifest:
            path = manifest[content_id].path

            if path.exists():
                return path

    # Fallback: <guide><reference type="cover" href="..."/></guide>
    guide = opf_soup.find("guide")

    if guide:
        ref = guide.find(
            "reference",
            attrs={"type": "cover"},
        )

        if ref and ref.get("href"):
            path = _resolve_href(opf_dir, ref["href"])

            if path.suffix.lower() in (
                ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
            ) and path.exists():
                return path

    return None


# ----------------------------------------------------------------------
# EPUB: table of contents (EPUB3 nav / EPUB2 NCX)
# ----------------------------------------------------------------------

def find_toc_entries(
    opf_soup: BeautifulSoup,
    manifest: dict[str, ManifestItem],
    spine_tag,
) -> list[dict]:
    """
    [{"title": str, "href": str, "level": int}, ...] を文書順に返す。
    hrefはOPFディレクトリからの相対パス(例: "chap01.xhtml#sec2")。
    """

    # EPUB3: manifest item with properties="nav"
    for item in manifest.values():
        if "nav" in item.properties.split() and item.path.exists():
            return _parse_nav_xhtml(item.path)

    # EPUB2: <spine toc="ncx-id"> -> manifest item -> toc.ncx
    ncx_id = spine_tag.get("toc")

    if ncx_id and ncx_id in manifest:
        ncx_path = manifest[ncx_id].path

        if ncx_path.exists():
            return _parse_toc_ncx(ncx_path)

    return []


def _parse_nav_xhtml(nav_path: Path) -> list[dict]:
    soup = BeautifulSoup(
        nav_path.read_text(encoding="utf-8", errors="replace"),
        "lxml",
    )

    nav = None

    for candidate in soup.find_all("nav"):
        nav_type = candidate.get("epub:type") or candidate.get("type") or ""

        if "toc" in nav_type.split():
            nav = candidate
            break

    if nav is None:
        nav = soup.find("nav")

    if nav is None:
        return []

    entries = []

    def walk(ol_tag, level: int):
        for li in ol_tag.find_all("li", recursive=False):
            a = li.find("a", recursive=False)

            if a and a.get("href"):
                entries.append({
                    "title": a.get_text(strip=True),
                    "href": a["href"],
                    "level": level,
                })

            nested_ol = li.find("ol", recursive=False)

            if nested_ol:
                walk(nested_ol, level + 1)

    top_ol = nav.find("ol")

    if top_ol:
        walk(top_ol, 0)

    return entries


def _parse_toc_ncx(ncx_path: Path) -> list[dict]:
    soup = BeautifulSoup(
        ncx_path.read_text(encoding="utf-8", errors="replace"),
        "xml",
    )

    entries = []

    def walk(parent_tag, level: int):
        for nav_point in parent_tag.find_all(
            "navPoint",
            recursive=False,
        ):
            label = nav_point.find("navLabel")
            content = nav_point.find("content")

            if label and content and content.get("src"):
                entries.append({
                    "title": label.get_text(strip=True),
                    "href": content["src"],
                    "level": level,
                })

            walk(nav_point, level + 1)

    nav_map = soup.find("navMap")

    if nav_map:
        walk(nav_map, 0)

    return entries


# ----------------------------------------------------------------------
# CSS: collection and detection (analyze, don't clobber)
# ----------------------------------------------------------------------

def rewrite_css_urls(css: str, css_path: Path) -> str:
    """
    CSS内の相対 url(...) 参照を file:// URLへ変換する。
    """

    def replace(match):
        quote = match.group(1) or ""
        value = match.group(2).strip()

        if value.startswith(("data:", "http:", "https:", "file:", "#")):
            return match.group(0)

        value = value.strip("\"'")

        parsed = urlparse(value)

        if parsed.scheme:
            return match.group(0)

        target = (css_path.parent / unquote(parsed.path)).resolve()

        if target.exists():
            encoded = target.as_uri()

            if parsed.fragment:
                encoded += "#" + parsed.fragment

            return f"url({quote}{encoded}{quote})"

        return match.group(0)

    pattern = r"url\(\s*([\"']?)(.*?)\1\s*\)"

    return re.sub(pattern, replace, css)


def collect_css(spine: list[Path]) -> str:
    """
    spineのXHTMLが参照するCSSファイルを読み込む。

    ChromiumがEPUB相対のスタイルシートURLを解決しなくて済むよう、
    生成するHTMLにCSSを埋め込む。
    """

    css_files = []

    for xhtml in spine:
        soup = BeautifulSoup(
            xhtml.read_text(encoding="utf-8", errors="replace"),
            "lxml",
        )

        for link in soup.find_all("link"):
            rel = [x.lower() for x in link.get("rel", [])]

            if "stylesheet" not in rel:
                continue

            href = link.get("href")

            if not href:
                continue

            parsed = urlparse(href)

            if parsed.scheme:
                continue

            css_path = (xhtml.parent / unquote(parsed.path)).resolve()

            if css_path.exists() and css_path not in css_files:
                css_files.append(css_path)

    result = []

    for css_path in css_files:
        css = css_path.read_text(encoding="utf-8", errors="replace")
        css = rewrite_css_urls(css, css_path)
        result.append(f"\n/* {css_path.name} */\n{css}\n")

    return "\n".join(result)


_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)

_ROOT_SELECTORS = {"html", "body", ":root", "html,body", "html, body"}


def _root_declarations(css: str) -> str:
    """
    セレクタがhtml/body/:rootのいずれかを含むルールの宣言ブロックを
    連結して返す。厳密なCSSカスケードの再現ではないが、「EPUB側で
    既に宣言されているか」を判定するには十分。
    """

    declarations = []

    for selectors, body in _RULE_RE.findall(css):
        selector_list = {
            s.strip().lower()
            for s in selectors.split(",")
        }

        if selector_list & _ROOT_SELECTORS:
            declarations.append(body)

    return "\n".join(declarations)


def detect_writing_mode(css: str) -> str | None:
    m = re.search(
        r"writing-mode\s*:\s*([a-zA-Z-]+)",
        _root_declarations(css),
    )

    return m.group(1).strip().lower() if m else None


def detect_line_break(css: str) -> str | None:
    m = re.search(
        r"line-break\s*:\s*([a-zA-Z-]+)",
        _root_declarations(css),
    )

    return m.group(1).strip().lower() if m else None


def detect_page_size(css: str) -> str | None:
    """
    収集したCSS中の `@page { size: ...; }` を探す。
    """

    m = re.search(r"@page[^{]*\{([^{}]*)\}", css)

    if not m:
        return None

    size_m = re.search(r"size\s*:\s*([^;]+)", m.group(1))

    return size_m.group(1).strip() if size_m else None


def detect_page_margin(css: str) -> str | None:
    m = re.search(r"@page[^{]*\{([^{}]*)\}", css)

    if not m:
        return None

    margin_m = re.search(r"(?<!-)margin\s*:\s*([^;]+)", m.group(1))

    return margin_m.group(1).strip() if margin_m else None


def has_ruby_rule(css: str) -> bool:
    return bool(re.search(r"(^|[,\s}])ruby\b", css)) or bool(
        re.search(r"(^|[,\s}])rt\b", css)
    )


PAGE_SIZE_KEYWORDS = {
    "a3", "a4", "a5", "a6", "b4", "b5",
    "letter", "legal", "ledger", "tabloid",
}


def resolve_page_size(value: str) -> tuple[dict, bool]:
    """
    CSSの `@page size` の値(またはCLI/呼び出し側の指定値)をPlaywrightの
    `page.pdf()` 用kwargsに変換する。(kwargs, landscape) を返す。
    """

    lowered = value.strip().lower()
    landscape = "landscape" in lowered

    tokens = [
        t for t in lowered.split()
        if t not in ("portrait", "landscape")
    ]

    if len(tokens) == 1 and tokens[0] in PAGE_SIZE_KEYWORDS:
        return {"format": tokens[0].upper()}, landscape

    if len(tokens) == 2:
        return {"width": tokens[0], "height": tokens[1]}, landscape

    # 認識できない場合はA5にフォールバックする(エラーにはしない)。
    return {"format": "A5"}, landscape


# ----------------------------------------------------------------------
# XHTML -> merged body HTML
# ----------------------------------------------------------------------

CHAPTER_HEADING_TAGS = ("h1", "h2")


def make_absolute_urls(soup: BeautifulSoup, xhtml: Path):
    """
    画像・フォント等の参照をfile:// URLに変換する。
    """

    for tag, attr in [
        ("img", "src"),
        ("image", "href"),
        ("image", "xlink:href"),
        ("source", "src"),
        ("video", "src"),
        ("audio", "src"),
    ]:
        for element in soup.find_all(tag):
            value = element.get(attr)

            if not value:
                continue

            if value.startswith(("data:", "http:", "https:", "file:")):
                continue

            parsed = urlparse(value)
            target = (xhtml.parent / unquote(parsed.path)).resolve()

            if target.exists():
                new_value = target.as_uri()

                if parsed.fragment:
                    new_value += "#" + parsed.fragment

                element[attr] = new_value


def _is_chapter_heading(tag) -> bool:
    if tag.name in CHAPTER_HEADING_TAGS:
        return True

    epub_type = tag.get("epub:type") or ""

    return "chapter" in epub_type.split()


def extract_chapter_section(
    xhtml: Path,
    spine_index: int,
) -> str:
    """
    spine中の1文書の<body>の中身を文字列で返す。以下を行う:
      - 画像・フォントのURLを絶対パス化する
      - TOCアンカーのフォールバック用に安定したidを振る(#spine-N)
      - その文書自身の先頭要素以外にある章見出しに `chapter-break`
        クラスを付与する(spine境界には別途改ページが入るため、これは
        1つのspine文書内に複数章が入っているケースのみを対象とする)
    """

    text = xhtml.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(text, "lxml")

    body = soup.find("body")

    if body is None:
        raise RuntimeError(f"{xhtml.name}: body がありません")

    make_absolute_urls(soup, xhtml)

    elements = body.find_all(True)
    first_element = elements[0] if elements else None

    for heading in body.find_all(CHAPTER_HEADING_TAGS + ("*",)):
        if not _is_chapter_heading(heading):
            continue

        if heading is first_element:
            continue

        classes = heading.get("class", [])

        if "chapter-break" not in classes:
            heading["class"] = classes + ["chapter-break"]

    inner_html = "".join(str(child) for child in body.contents)

    return (
        f'<section class="epub-chapter" '
        f'data-spine-index="{spine_index}" '
        f'id="spine-{spine_index}">\n'
        f"{inner_html}\n"
        f"</section>"
    )


def resolve_toc_href(
    href: str,
    opf_dir: Path,
    spine: list[Path],
) -> str:
    """
    EPUB相対のTOC href(`#fragment` を含みうる)を、生成した単一HTML内の
    アンカーに変換する。元ファイルにフラグメントが存在すればそのまま
    引き継がれる(bodyのHTMLをid込みでそのままコピーしているため)。
    存在しなければspineセクション自身のidにフォールバックする。
    """

    parsed = urlparse(href)
    target_path = _resolve_href(opf_dir, parsed.path)

    for index, spine_path in enumerate(spine):
        if spine_path == target_path:
            if parsed.fragment:
                return f"#{parsed.fragment}"

            return f"#spine-{index}"

    # spineのどの文書にも一致しない(非XHTMLリソースを指している等)場合は
    # 実害のないダミーアンカーのままにする。
    return "#"


def build_toc_html(
    toc_entries: list[dict],
    opf_dir: Path,
    spine: list[Path],
) -> str:
    if not toc_entries:
        return ""

    items = []

    for entry in toc_entries:
        anchor = resolve_toc_href(entry["href"], opf_dir, spine)
        indent_class = f'toc-level-{min(entry["level"], 3)}'

        items.append(
            f'<li class="{indent_class}">'
            f'<a href="{anchor}">{entry["title"]}</a>'
            f"</li>"
        )

    return (
        '<section class="epub-toc">\n'
        "<h1>目次</h1>\n"
        "<ol>\n" + "\n".join(items) + "\n</ol>\n"
        "</section>"
    )


def build_cover_html(cover: Path | None) -> str:
    if cover is None:
        return ""

    return (
        '<section class="epub-cover">\n'
        f'<img src="{cover.as_uri()}" alt="cover">\n'
        "</section>"
    )


# ----------------------------------------------------------------------
# HTML document assembly
# ----------------------------------------------------------------------

def build_style_block(
    epub_css: str,
    font_size: str,
    line_height: str,
    margin: float,
    writing_mode_override: str | None,
    page_size_override: str | None,
) -> tuple[str, dict, bool]:
    """
    <head>の中身(EPUBのCSS + 印刷用CSS)を組み立てる。表紙/TOCのみの
    仮レンダリング(実際に何ページ占めるか測定するため)と本番レンダリング
    の両方で共有し、ページ割りを一致させる。

    (head_html, pdf_size_kwargs, landscape) を返す。
    """

    detected_writing_mode = writing_mode_override or detect_writing_mode(epub_css)
    writing_mode = detected_writing_mode or "vertical-rl"

    detected_line_break = detect_line_break(epub_css)
    line_break_rule = (
        "" if detected_line_break else "line-break: strict;"
    )

    ruby_already_styled = has_ruby_rule(epub_css)

    detected_size = page_size_override or detect_page_size(epub_css)
    size_kwargs, landscape = resolve_page_size(detected_size or "A5")

    detected_margin = detect_page_margin(epub_css)
    page_margin_css = detected_margin or f"{margin}mm"

    ruby_css = (
        ""
        if ruby_already_styled
        else """
    ruby { ruby-position: over; }
    rt { font-size: 0.5em; line-height: 1; white-space: nowrap; }
    """
    )

    custom_css = f"""
    /* ==============================================================
       印刷用スタイルシート。
       EPUB自身のCSSが既に宣言していない項目だけを補う。!important は
       使わないため、同等以上の詳細度を持つEPUB側のルールが存在すれば
       そちらが優先される。
       ============================================================== */

    @page {{
        size: {detected_size or "A5"};
        margin: {page_margin_css};
    }}

    html, body {{
        margin: 0;
        padding: 0;
        background: white;
    }}

    body {{
        writing-mode: {writing_mode};
        text-orientation: mixed;
        {line_break_rule}
        word-break: normal;
        overflow-wrap: normal;

        font-size: {font_size};
        line-height: {line_height};

        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
    }}
    {ruby_css}

    h1, h2, h3, h4, h5, h6 {{
        break-inside: avoid;
        page-break-inside: avoid;
    }}

    /* 章の区切り: spine文書の境界 */
    .epub-chapter + .epub-chapter {{
        break-before: page;
        page-break-before: always;
    }}

    /* ...と、検出した文書内の章見出し */
    .chapter-break {{
        break-before: page;
        page-break-before: always;
    }}

    .epub-cover {{
        break-after: page;
        page-break-after: always;

        display: flex;
        align-items: center;
        justify-content: center;
        height: 100%;
    }}

    .epub-cover img {{
        max-width: 100%;
        max-height: 100%;
    }}

    .epub-toc {{
        break-after: page;
        page-break-after: always;
    }}

    .epub-toc ol {{
        list-style: none;
    }}

    .epub-toc .toc-level-1 {{ padding-inline-start: 1em; }}
    .epub-toc .toc-level-2 {{ padding-inline-start: 2em; }}
    .epub-toc .toc-level-3 {{ padding-inline-start: 3em; }}

    img {{
        max-width: 100%;
        max-height: 100%;
    }}

    table {{
        max-width: 100%;
    }}

    nav.toc, .navigation {{
        display: none;
    }}
    """

    head_html = f"""
<meta charset="UTF-8">
<style>
{epub_css}
</style>
<style>
{custom_css}
</style>
"""

    return head_html, size_kwargs, landscape


def wrap_document(head_html: str, body_sections: list[str]) -> str:
    return f"""
<!DOCTYPE html>
<html lang="ja">
<head>
{head_html}
</head>
<body>
{''.join(body_sections)}
</body>
</html>
"""


# ----------------------------------------------------------------------
# PDF page numbers
# ----------------------------------------------------------------------

def find_japanese_font() -> Path | None:
    candidates = [
        Path("/System/Library/Fonts/ヒラギノ明朝 ProN.ttc"),
        Path("/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"),
        Path("/System/Library/Fonts/Hiragino Mincho ProN.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/Library/Fonts/NotoSansJP-Regular.ttf"),
        Path.home() / "Library/Fonts/NotoSansJP-Regular.ttf",
    ]

    for path in candidates:
        if path.exists():
            return path

    return None


def add_page_numbers(
    input_pdf: Path,
    output_pdf: Path,
    position: str,
    skip_pages: int,
):
    """
    ReportLab+pypdfでページ番号を重ね書きする。

    skip_pages: 番号を振らない先頭ページ数(表紙・目次)。
    """

    reader = PdfReader(str(input_pdf))
    writer = PdfWriter()

    font_path = find_japanese_font()
    font_name = "Helvetica"

    if font_path:
        try:
            pdfmetrics.registerFont(TTFont("JapaneseFont", str(font_path)))
            font_name = "JapaneseFont"
        except Exception:
            pass

    for page_index, page in enumerate(reader.pages):
        if page_index < skip_pages:
            writer.add_page(page)
            continue

        width = float(page.mediabox.width)
        height = float(page.mediabox.height)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp:
            overlay_path = Path(temp.name)

        c = canvas.Canvas(str(overlay_path), pagesize=(width, height))
        c.setFont(font_name, 9)

        page_number = str(page_index + 1 - skip_pages)
        text_width = pdfmetrics.stringWidth(page_number, font_name, 9)

        if position == "bottom":
            x = (width - text_width) / 2
            y = 12 * 2.83465

        elif position == "outer":
            margin = 12 * 2.83465

            if (page_index + 1 - skip_pages) % 2:
                x = width - margin - text_width
            else:
                x = margin

            y = 12 * 2.83465

        else:
            raise ValueError(f"Unknown page-number position: {position}")

        c.drawString(x, y, page_number)
        c.save()

        overlay_reader = PdfReader(str(overlay_path))
        # writerに紐付けた後のページに対してmerge_pageする(先にmergeして
        # からwriterへ渡す順序はpypdf 7.0で削除予定のため)。
        added_page = writer.add_page(page)
        added_page.merge_page(overlay_reader.pages[0])

        overlay_path.unlink(missing_ok=True)

    with output_pdf.open("wb") as f:
        writer.write(f)


# ----------------------------------------------------------------------
# Main conversion
# ----------------------------------------------------------------------

def _file_url(path: Path) -> str:
    return path.resolve().as_uri()


async def _convert_epub(
    epub_path: Path,
    output_pdf: Path,
    *,
    font_size: str,
    line_height: str,
    margin: float,
    page_number_position: str,
    writing_mode_override: str | None,
    page_size_override: str | None,
    include_cover: bool,
    include_toc: bool,
) -> None:
    with tempfile.TemporaryDirectory(prefix="narou-dl-pdf-") as temp_dir:
        temp_dir = Path(temp_dir)

        with zipfile.ZipFile(epub_path, "r") as z:
            z.extractall(temp_dir)

        epub = parse_epub(temp_dir)
        epub_css = collect_css(epub.spine)

        head_html, size_kwargs, landscape = build_style_block(
            epub_css=epub_css,
            font_size=font_size,
            line_height=line_height,
            margin=margin,
            writing_mode_override=writing_mode_override,
            page_size_override=page_size_override,
        )

        opf_dir = epub.opf.parent

        cover_html = build_cover_html(epub.cover) if include_cover else ""
        toc_html = (
            build_toc_html(epub.toc_entries, opf_dir, epub.spine)
            if include_toc
            else ""
        )
        front_matter_sections = [s for s in (cover_html, toc_html) if s]

        chapters = [
            extract_chapter_section(xhtml, index)
            for index, xhtml in enumerate(epub.spine)
        ]

        pdf_kwargs = dict(
            landscape=landscape,
            print_background=True,
            prefer_css_page_size=True,
            margin={
                "top": f"{margin}mm",
                "right": f"{margin}mm",
                "bottom": f"{margin}mm",
                "left": f"{margin}mm",
            },
            **size_kwargs,
        )

        async def render(body_sections: list[str], out_path: Path):
            html = wrap_document(head_html, body_sections)
            html_path = out_path.with_suffix(".html")
            html_path.write_text(html, encoding="utf-8")

            page = await context.new_page()
            await page.goto(_file_url(html_path), wait_until="networkidle")
            await page.evaluate(
                """
                async () => {
                    if (document.fonts) {
                        await document.fonts.ready;
                    }
                }
                """
            )
            await page.pdf(path=str(out_path), **pdf_kwargs)
            await page.close()

        raw_pdf = temp_dir / "chromium.pdf"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(locale="ja-JP")

            # 表紙・TOCは先に単独でレンダリングし、実際に何ページ占めるかを
            # 測ってからページ番号のスキップ数として使う(TOCが複数ページに
            # 渡る作品もあるため、1セクション=1ページと決め打ちできない)。
            if front_matter_sections:
                front_matter_pdf = temp_dir / "front_matter.pdf"
                await render(front_matter_sections, front_matter_pdf)
                skip_pages = len(PdfReader(str(front_matter_pdf)).pages)
            else:
                skip_pages = 0

            await render(front_matter_sections + chapters, raw_pdf)

            await browser.close()

        add_page_numbers(raw_pdf, output_pdf, page_number_position, skip_pages)


def build_pdf(
    epub_path: str | Path,
    pdf_path: str | Path,
    *,
    font_size: str = "9pt",
    line_height: str = "1.8",
    margin: float = 15,
    page_number_position: str = "bottom",
    writing_mode: str | None = None,
    page_size: str | None = None,
    include_cover: bool = True,
    include_toc: bool = True,
) -> None:
    """生成済みのEPUBファイルをChromiumで描画し、PDFに変換する。

    書字方向・判型・余白は指定しない限りEPUB自身のCSSから自動検出する
    (詳細はモジュールdocstring参照)。

    Args:
        epub_path: 変換元のEPUBファイル。
        pdf_path: 出力するPDFファイル。
        font_size: EPUBのCSSが指定していない場合のフォールバック値。
        line_height: 同上。
        margin: EPUBのCSSが `@page` でmarginを指定していない場合の
            フォールバック値(mm)。
        page_number_position: `"bottom"` (中央下)または `"outer"`
            (奇数ページ/偶数ページで左右を入れ替える、右開き想定)。
        writing_mode: `None` ならEPUBのCSSから自動検出する。
            `"vertical-rl"` / `"horizontal-tb"` を指定すると強制する。
        page_size: `None` ならEPUBのCSSの `@page size` から自動検出し、
            見つからなければA5にフォールバックする。
        include_cover: EPUBに表紙画像があれば1ページ目に描画する。
        include_toc: EPUB3のnavまたはEPUB2のNCXから目次ページを生成する。

    Raises:
        PdfEngineError: Chromiumが未インストール、またはEPUBの構造が
            壊れている等でレンダリングに失敗した場合。
    """

    try:
        asyncio.run(
            _convert_epub(
                Path(epub_path),
                Path(pdf_path),
                font_size=font_size,
                line_height=line_height,
                margin=margin,
                page_number_position=page_number_position,
                writing_mode_override=writing_mode,
                page_size_override=page_size,
                include_cover=include_cover,
                include_toc=include_toc,
            )
        )
    except PdfEngineError:
        raise
    except Exception as exc:
        raise PdfEngineError(str(exc)) from exc
