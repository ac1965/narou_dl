"""pdf_builder.py(ChromiumでEPUBをPDF化するエンジン)に対するテスト。

playwrightは任意インストール(pdf extra)のため、未インストール環境では
このファイル全体をスキップする。Chromium本体(ブラウザバイナリ)は
`playwright install chromium`を別途実行しないと使えないさらに重い依存の
ため、`make test`はそのインストールを強制しない方針にしている。そのため
実際にChromiumを起動するテスト(test_build_pdf_end_to_end_smoke)だけは、
Chromiumが無い環境ではエラーではなくスキップにする。それ以外のテストは
EPUBの解析・CSS検出・HTML組み立てといった純粋なロジックを対象にしており、
Chromiumなしで実行できる。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from narou_dl.api import NovelInfo  # noqa: E402
from narou_dl.epub_builder import build_epub  # noqa: E402
from narou_dl.pdf_builder import (  # noqa: E402
    PdfEngineError,
    build_cover_html,
    build_pdf,
    build_style_block,
    build_toc_html,
    detect_line_break,
    detect_page_margin,
    detect_page_size,
    detect_writing_mode,
    extract_chapter_section,
    has_ruby_rule,
    parse_epub,
    resolve_page_size,
    resolve_toc_href,
)
from narou_dl.scraper import Episode  # noqa: E402


def _novel_info() -> NovelInfo:
    return NovelInfo(
        ncode="n0000aa",
        title="テスト作品",
        writer="テスト作者",
        story="あらすじ本文",
        general_all_no=2,
        novel_type=1,
        end=0,
    )


def _build_sample_epub(tmp_path: Path, *, vertical: bool = True) -> Path:
    episodes = [
        Episode(index=1, subtitle="第一話", paragraphs=["ほんぶんいち"]),
        Episode(index=2, subtitle="第二話", paragraphs=["ほんぶんに"]),
    ]
    epub_path = tmp_path / "test.epub"
    build_epub(
        _novel_info(),
        episodes,
        str(epub_path),
        vertical=vertical,
        chapter_map={1: "第一章", 2: "第一章"},
        embed_images=False,
    )
    return epub_path


def _extract_epub(epub_path: Path, dest_dir: Path) -> Path:
    with zipfile.ZipFile(epub_path) as z:
        z.extractall(dest_dir)
    return dest_dir


# ----------------------------------------------------------------------
# CSS detection(EPUB自身のCSSを尊重する自動判定ロジック)
# ----------------------------------------------------------------------

def test_detect_writing_mode_finds_declared_value():
    css = "html, body { color: black; writing-mode: vertical-rl; }"
    assert detect_writing_mode(css) == "vertical-rl"


def test_detect_writing_mode_none_when_undeclared():
    assert detect_writing_mode("p { color: red; }") is None


def test_detect_writing_mode_ignores_non_root_selector():
    # bodyやhtml以外のセレクタへの指定は「EPUB全体の書字方向」とは
    # 見なさない(本文中の一部要素だけ縦中横、等のケースを誤検出しない)
    css = ".tcy { writing-mode: vertical-rl; }"
    assert detect_writing_mode(css) is None


def test_detect_line_break_finds_declared_value():
    css = "body { line-break: strict; }"
    assert detect_line_break(css) == "strict"


def test_detect_page_size_from_at_page_rule():
    css = "@page { size: 128mm 182mm; margin: 10mm; }"
    assert detect_page_size(css) == "128mm 182mm"


def test_detect_page_size_none_without_at_page():
    assert detect_page_size("body { color: black; }") is None


def test_detect_page_margin_from_at_page_rule():
    css = "@page { size: A5; margin: 12mm; }"
    assert detect_page_margin(css) == "12mm"


def test_has_ruby_rule_true_when_ruby_styled():
    assert has_ruby_rule("ruby { ruby-position: over; }") is True
    assert has_ruby_rule("rt { font-size: 0.6em; }") is True


def test_has_ruby_rule_false_when_unstyled():
    assert has_ruby_rule("p { color: black; }") is False


def test_resolve_page_size_keyword():
    kwargs, landscape = resolve_page_size("A5")
    assert kwargs == {"format": "A5"}
    assert landscape is False


def test_resolve_page_size_explicit_dimensions():
    kwargs, landscape = resolve_page_size("128mm 182mm")
    assert kwargs == {"width": "128mm", "height": "182mm"}
    assert landscape is False


def test_resolve_page_size_landscape_keyword():
    _, landscape = resolve_page_size("A4 landscape")
    assert landscape is True


def test_resolve_page_size_unrecognized_falls_back_to_a5():
    kwargs, _ = resolve_page_size("not-a-size")
    assert kwargs == {"format": "A5"}


# ----------------------------------------------------------------------
# narou_dlが実際に生成するEPUBのCSSと組み合わせた検出
# ----------------------------------------------------------------------

def test_writing_mode_auto_detected_from_narou_dl_vertical_epub(tmp_path):
    epub_path = _build_sample_epub(tmp_path, vertical=True)
    extract_dir = _extract_epub(epub_path, tmp_path / "extracted_v")
    epub = parse_epub(extract_dir)

    from narou_dl.pdf_builder import collect_css

    css = collect_css(epub.spine)
    assert detect_writing_mode(css) == "vertical-rl"


def test_writing_mode_auto_detected_from_narou_dl_horizontal_epub(tmp_path):
    epub_path = _build_sample_epub(tmp_path, vertical=False)
    extract_dir = _extract_epub(epub_path, tmp_path / "extracted_h")
    epub = parse_epub(extract_dir)

    from narou_dl.pdf_builder import collect_css

    css = collect_css(epub.spine)
    assert detect_writing_mode(css) == "horizontal-tb"


def test_parse_epub_finds_spine_and_toc(tmp_path):
    epub_path = _build_sample_epub(tmp_path)
    extract_dir = _extract_epub(epub_path, tmp_path / "extracted")
    epub = parse_epub(extract_dir)

    # 章区切りページ等が挟まるため件数は実装依存。2話分は必ず含まれる。
    assert len(epub.spine) >= 2
    assert len(epub.toc_entries) >= 1
    # narou_dlのEPUBビルダーは表紙画像を作らない
    assert epub.cover is None


# ----------------------------------------------------------------------
# HTML組み立て(章検出・目次・表紙)
# ----------------------------------------------------------------------

def test_extract_chapter_section_marks_in_file_chapter_headings(tmp_path):
    xhtml = tmp_path / "combined.xhtml"
    xhtml.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>第一章</h1><p>本文一</p>
<h1>第二章</h1><p>本文二</p>
</body></html>""",
        encoding="utf-8",
    )

    section_html = extract_chapter_section(xhtml, 0)

    # 先頭の見出し(このspine文書自身の最初の要素)は改ページ不要
    assert '<h1>第一章</h1>' in section_html
    # 2つ目の見出しは1ファイル内の章区切りとして改ページ対象になる
    assert '<h1 class="chapter-break">第二章</h1>' in section_html


def test_resolve_toc_href_prefers_original_fragment(tmp_path):
    spine = [tmp_path / "c1.xhtml", tmp_path / "c2.xhtml"]

    assert resolve_toc_href("c2.xhtml#sec1", tmp_path, spine) == "#sec1"
    assert resolve_toc_href("c1.xhtml", tmp_path, spine) == "#spine-0"


def test_resolve_toc_href_unknown_target_is_harmless():
    assert resolve_toc_href("missing.xhtml", Path("/tmp"), []) == "#"


def test_build_toc_html_empty_when_no_entries():
    assert build_toc_html([], Path("/tmp"), []) == ""


def test_build_toc_html_lists_entries(tmp_path):
    spine = [tmp_path / "c1.xhtml"]
    entries = [{"title": "第一話", "href": "c1.xhtml", "level": 0}]

    html = build_toc_html(entries, tmp_path, spine)
    assert "第一話" in html
    assert 'href="#spine-0"' in html


def test_build_cover_html_empty_when_none():
    assert build_cover_html(None) == ""


def test_build_cover_html_embeds_file_uri(tmp_path):
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"\x89PNG\r\n")

    html = build_cover_html(cover)
    assert cover.as_uri() in html


# ----------------------------------------------------------------------
# スタイルブロック(EPUBのCSSを尊重しつつ足りない項目だけ補う)
# ----------------------------------------------------------------------

def test_build_style_block_auto_detects_writing_mode_and_size():
    css = "html, body { writing-mode: vertical-rl; } @page { size: A6; }"
    head_html, size_kwargs, landscape = build_style_block(
        css, "9pt", "1.8", 15, None, None,
    )

    assert "writing-mode: vertical-rl" in head_html
    assert size_kwargs == {"format": "A6"}
    assert landscape is False


def test_build_style_block_override_wins_over_epub_css():
    css = "html, body { writing-mode: vertical-rl; }"
    head_html, _, _ = build_style_block(
        css, "9pt", "1.8", 15, "horizontal-tb", None,
    )

    assert "writing-mode: horizontal-tb" in head_html


def test_build_style_block_falls_back_when_undetected():
    head_html, size_kwargs, _ = build_style_block("", "9pt", "1.8", 15, None, None)

    assert "writing-mode: vertical-rl" in head_html
    assert size_kwargs == {"format": "A5"}


# ----------------------------------------------------------------------
# エンドツーエンド(実際にChromiumを起動する。未インストール環境ではスキップ)
# ----------------------------------------------------------------------

def test_build_pdf_end_to_end_smoke(tmp_path):
    epub_path = _build_sample_epub(tmp_path)
    pdf_path = tmp_path / "test.pdf"

    try:
        build_pdf(epub_path, pdf_path)
    except PdfEngineError as exc:
        pytest.skip(f"Chromiumが利用できないためスキップします: {exc}")

    assert pdf_path.exists()
    data = pdf_path.read_bytes()
    assert data.startswith(b"%PDF")
    assert b"%%EOF" in data
