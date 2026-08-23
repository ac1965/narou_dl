"""pdf_builder.py(Pure Pythonの縦書きPDF組版エンジン)に対するテスト。

reportlabは任意インストール(pdf extra)のため、未インストール環境では
このファイル全体をスキップする。
"""
from __future__ import annotations

import pytest

pytest.importorskip("reportlab")

from narou_dl.api import NovelInfo  # noqa: E402
from narou_dl.pdf_builder import (  # noqa: E402
    BoutenSegment,
    ImageSegment,
    PlainSegment,
    RubySegment,
    _atomize,
    _parse_paragraph,
    build_pdf,
)
from narou_dl.scraper import Episode  # noqa: E402


def test_atomize_combines_digit_runs_into_tcy():
    atoms = _atomize("第12話")
    kinds = [(a.text, a.kind) for a in atoms]
    assert kinds == [("第", "char"), ("12", "tcy"), ("話", "char")]


def test_atomize_splits_long_digit_runs_at_four_chars():
    # tcyは1〜4桁まで。5桁目は次のtcy(1桁)として別セルになる
    atoms = _atomize("12345")
    kinds = [(a.text, a.kind) for a in atoms]
    assert kinds == [("1234", "tcy"), ("5", "tcy")]


def test_atomize_marks_ascii_letters_as_latin():
    atoms = _atomize("AB")
    assert [(a.text, a.kind) for a in atoms] == [("A", "latin"), ("B", "latin")]


def test_parse_paragraph_blank_line_becomes_ideographic_space():
    segments = _parse_paragraph("")
    assert len(segments) == 1
    assert isinstance(segments[0], PlainSegment)
    assert segments[0].atoms[0].text == "　"


def test_parse_paragraph_extracts_ruby_base_and_reading():
    segments = _parse_paragraph(
        '<ruby>漢字<rp>（</rp><rt>かんじ</rt><rp>）</rp></ruby>のテスト'
    )
    ruby_segments = [s for s in segments if isinstance(s, RubySegment)]
    assert len(ruby_segments) == 1
    assert "".join(a.text for a in ruby_segments[0].atoms) == "漢字"
    assert ruby_segments[0].ruby == "かんじ"


def test_parse_paragraph_extracts_bouten():
    segments = _parse_paragraph('傍点<em class="emphasisDots">強調</em>です')
    bouten_segments = [s for s in segments if isinstance(s, BoutenSegment)]
    assert len(bouten_segments) == 1
    assert "".join(a.text for a in bouten_segments[0].atoms) == "強調"


def test_parse_paragraph_extracts_image_url():
    segments = _parse_paragraph('<img src="https://example.com/a.png" alt="挿絵"/>')
    image_segments = [s for s in segments if isinstance(s, ImageSegment)]
    assert len(image_segments) == 1
    assert image_segments[0].url == "https://example.com/a.png"


def _novel_info() -> NovelInfo:
    return NovelInfo(
        ncode="n0000aa",
        title="テスト作品",
        writer="テスト作者",
        story="あらすじ本文",
        general_all_no=1,
        novel_type=1,
        end=0,
    )


def test_build_pdf_produces_a_valid_pdf_file(tmp_path):
    episodes = [
        Episode(index=1, subtitle="第一話", paragraphs=["本文の一行目。", "", "本文の二行目。"]),
        Episode(index=2, subtitle="第二話", paragraphs=["別の話の本文。"]),
    ]
    output_path = tmp_path / "test.pdf"

    build_pdf(
        _novel_info(),
        episodes,
        str(output_path),
        vertical=True,
        chapter_map={1: "第一章"},
        embed_images=False,
    )

    assert output_path.exists()
    data = output_path.read_bytes()
    assert data.startswith(b"%PDF")
    assert b"%%EOF" in data
    # 2話分程度なら複数ページに分かれるはず(タイトルページ+本文)
    assert data.count(b"/Type /Page") >= 2


def test_build_pdf_horizontal_mode_does_not_raise(tmp_path):
    episodes = [Episode(index=1, subtitle="第一話", paragraphs=["本文"])]
    output_path = tmp_path / "yoko.pdf"

    build_pdf(_novel_info(), episodes, str(output_path), vertical=False, embed_images=False)

    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"%PDF")


def test_build_pdf_without_images_does_not_touch_network(tmp_path):
    episodes = [
        Episode(index=1, subtitle="話", paragraphs=['<img src="https://example.invalid/x.png"/>本文']),
    ]
    output_path = tmp_path / "noimg.pdf"

    build_pdf(_novel_info(), episodes, str(output_path), embed_images=False)

    assert output_path.exists()
