"""epub_builder.py に対するテスト。

段落変換の純粋なロジック(タグを含まない部分)を単体テストしたうえで、
build_epub()自体もネットワークアクセス無し(embed_images=False)で実際に
1冊分書き出し、ebooklibで正しく読み戻せることを確認する。
"""
from __future__ import annotations

from ebooklib import ITEM_DOCUMENT, epub

from narou_dl.api import NovelInfo
from narou_dl.epub_builder import (
    _combine_digits,
    _is_blank_paragraph,
    _paragraphs_to_html,
    build_epub,
)
from narou_dl.scraper import Episode


def test_is_blank_paragraph_detects_empty_and_br_only():
    assert _is_blank_paragraph("") is True
    assert _is_blank_paragraph("<br/>") is True
    assert _is_blank_paragraph("　") is True
    assert _is_blank_paragraph("本文") is False


def test_combine_digits_wraps_runs_in_tcy_span_when_vertical():
    result = _combine_digits("第12話", vertical=True)
    assert result == "第<span class=\"tcy\">12</span>話"


def test_combine_digits_noop_when_horizontal():
    result = _combine_digits("第12話", vertical=False)
    assert result == "第12話"


def test_combine_digits_wraps_only_text_outside_tags():
    html = '<img src="a1.png" alt="12"/>訪れたのは3日後'
    result = _combine_digits(html, vertical=True, already_html=True)
    # タグの内側(属性値)の数字は変換対象に含まれない
    assert 'src="a1.png"' in result
    assert 'alt="12"' in result
    # タグの外側のテキスト部分の数字は縦中横化される
    assert '<span class="tcy">3</span>日後' in result


def test_paragraphs_to_html_renders_one_p_per_paragraph():
    html = _paragraphs_to_html(["行1", "", "行2"], vertical=False)
    assert html == '<p>行1</p>\n<p class="blank">　</p>\n<p>行2</p>'


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


def test_build_epub_produces_valid_readable_epub(tmp_path):
    # 縦中横化(_combine_digits)が別途テスト済みのため、ここでは数字を含めず
    # 本文がそのまま読み戻せることだけを確認する
    episodes = [
        Episode(index=1, subtitle="第一話", paragraphs=["ほんぶんいち"]),
        Episode(index=2, subtitle="第二話", paragraphs=["ほんぶんに"]),
    ]
    output_path = tmp_path / "test.epub"

    build_epub(
        _novel_info(),
        episodes,
        str(output_path),
        vertical=True,
        chapter_map={1: "第一章", 2: "第一章"},
        embed_images=False,
    )

    assert output_path.exists()

    book = epub.read_epub(str(output_path))
    assert book.get_metadata("DC", "title")[0][0] == "テスト作品"
    assert book.get_metadata("DC", "creator")[0][0] == "テスト作者"

    html_items = [item for item in book.get_items() if item.get_type() == ITEM_DOCUMENT]
    combined = "".join(item.get_content().decode("utf-8") for item in html_items)
    assert "第一話" in combined
    assert "ほんぶんいち" in combined
    assert "第二話" in combined
    assert "ほんぶんに" in combined


def test_build_epub_horizontal_mode_does_not_raise(tmp_path):
    episodes = [Episode(index=1, subtitle="第一話", paragraphs=["本文"])]
    output_path = tmp_path / "test_yoko.epub"

    build_epub(
        _novel_info(), episodes, str(output_path), vertical=False, embed_images=False,
    )

    assert output_path.exists()
