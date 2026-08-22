"""aozora.py の青空文庫記法変換に対するテスト。"""
from __future__ import annotations

from narou_dl.aozora import (
    build_novel_text,
    chapter_heading,
    em_to_aozora,
    episode_heading,
    escape_literal_chuki_chars,
    img_to_aozora,
    paragraph_to_aozora,
    ruby_to_aozora,
)
from narou_dl.scraper import Episode


def test_ruby_to_aozora_converts_ruby_tag_with_rb():
    html = "<ruby><rb>漢字</rb><rp>（</rp><rt>かんじ</rt><rp>）</rp></ruby>"
    assert ruby_to_aozora(html) == "｜漢字《かんじ》"


def test_ruby_to_aozora_handles_omitted_rb():
    # なろうの<ruby>は<rb>が省略され、対象文字列がテキストノードのまま
    # <rt>の前に置かれることがある
    html = "<ruby>漢字<rp>（</rp><rt>かんじ</rt><rp>）</rp></ruby>"
    assert ruby_to_aozora(html) == "｜漢字《かんじ》"


def test_em_to_aozora_converts_bouten():
    html = '傍点<em class="emphasisDots">強調</em>です'
    assert em_to_aozora(html) == "傍点［＃傍点］強調［＃傍点終わり］です"


def test_img_to_aozora_uses_registry_path():
    html = '<img src="https://example.com/a.png" alt="挿絵"/>'
    registry = {"https://example.com/a.png": "images/illust_0001.jpg"}
    assert img_to_aozora(html, registry) == "［＃挿絵（images/illust_0001.jpg）入る］"


def test_img_to_aozora_drops_tag_when_not_in_registry():
    html = '<img src="https://example.com/missing.png" alt="挿絵"/>'
    assert img_to_aozora(html, {}) == ""


def test_escape_literal_chuki_chars_escapes_pipe_and_brackets():
    text = "《五龍将》と呼ばれる｜男"
    escaped = escape_literal_chuki_chars(text)
    assert escaped == "※《五龍将※》と呼ばれる※｜男"


def test_paragraph_to_aozora_combines_all_conversions():
    text = (
        "地の文の《装飾》と"
        "<ruby>漢字<rp>（</rp><rt>かんじ</rt><rp>）</rp></ruby>と"
        '<em class="emphasisDots">強調</em>'
    )
    result = paragraph_to_aozora(text, {})
    # 地の文由来の《》はエスケープされ、ルビ構文の《》とは区別される
    assert result == "地の文の※《装飾※》と｜漢字《かんじ》と［＃傍点］強調［＃傍点終わり］"


def test_paragraph_to_aozora_returns_blank_for_empty_string():
    assert paragraph_to_aozora("", {}) == ""


def test_chapter_heading_format():
    assert chapter_heading("第一章") == "［＃改ページ］\n［＃３字下げ］［＃中見出し］第一章［＃中見出し終わり］\n"


def test_episode_heading_format():
    assert episode_heading("第一話") == "［＃小見出し］第一話［＃小見出し終わり］"


def test_build_novel_text_includes_title_author_story_and_chapters():
    episodes = [
        Episode(index=1, subtitle="第一話", paragraphs=["本文1"]),
        Episode(index=2, subtitle="第二話", paragraphs=["本文2"]),
    ]
    text = build_novel_text(
        title="作品名",
        author="作者名",
        story="あらすじ本文",
        episodes=episodes,
        chapter_map={1: "第一章"},
        image_registry={},
    )

    assert text.startswith("作品名\n\n作者名\n\n")
    assert "あらすじ" in text
    assert "第一章" in text
    assert "第一話" in text
    assert "本文1" in text
    assert "第二話" in text
    assert "本文2" in text


def test_build_novel_text_omits_story_section_when_blank():
    episodes = [Episode(index=1, subtitle="第一話", paragraphs=["本文"])]
    text = build_novel_text(
        title="作品名", author="作者名", story="", episodes=episodes,
        chapter_map={}, image_registry={},
    )
    assert "あらすじ" not in text
