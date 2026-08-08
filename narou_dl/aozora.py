"""scraper.Episode の本文(HTML断片)を青空文庫記法テキストに変換する。

AozoraEpub3(改造版)をEPUB化バックエンドとして使う際に、その入力形式
である青空文庫記法テキストを組み立てるために使う(aozoraepub3_backend.py
と対で使用する)。

変換規則は Ruby版 narou の lib/html.rb (HTML#to_aozora 系のメソッド群) と
可能な限り同じアルゴリズムに揃えている:

    html.rb の対応          | このモジュールの対応
    ------------------------|--------------------------------
    ruby_to_aozora           | ruby_to_aozora
    em_to_sesame(傍点)        | em_to_aozora
    img_to_aozora             | img_to_aozora
    delete_tag                | strip_remaining_tags

なろうの<ruby>には<rb>が省略されている場合がある(ルビ対象の文字列が
そのままテキストノードとして<rt>の前に置かれる)ため、<rb>の有無どちらの
パターンにも対応する。

章見出しの注記形式(［＃３字下げ］［＃中見出し］...［＃中見出し終わり］)は
Ruby版 narou の lib/converterbase.rb の enchant_midashi が生成する形式に
合わせている。AozoraEpub3側がこの形式をEPUBのTOCの見出しとして認識する。

このモジュールが対応していないもの(将来の課題):
    - 画像の回り込み・キャプション付き挿絵注記(拡張構文)。
      基本形の ［＃挿絵（path）入る］ のみサポートする。
    - 外字・縦中横は青空文庫記法側で明示する必要が無い
      (AozoraEpub3側が変換後のテキストから自動判定するため、
      このモジュールでは一切関与しない)。

地の文に含まれる｜《》のエスケープについて:
    なろう作品では｜《》(パイプ・二重山括弧)が装飾的な記号として地の文に
    そのまま使われることがある(例:「《五龍将》と呼ばれる」)。これらは
    青空文庫記法のルビ構文の予約文字と衝突するため、エスケープしないと
    AozoraEpub3側で「ルビ開始文字無し」という警告とともに正しく変換
    されない(実際に「無職転生」で確認された不具合)。

    AozoraEpub3Converter.java の CharUtils#isEscapedChar によれば、
    特殊文字の直前に｢※｣が奇数個続く場合はエスケープされた(=リテラルの)
    文字として扱われる。このモジュールでは、地の文由来の｜《》に対して
    この方式(直前に※を1つ付与)でエスケープしてから ruby_to_aozora を
    適用する(escape_literal_chuki_chars → ruby_to_aozora の順)。
    Ruby版 narou の html.rb は ≪≫ への一時置換 → 外字注記化という
    2段階の方式を取っているが、AozoraEpub3が直接解釈できる※エスケープの
    方がシンプルなためこちらを採用した。
"""
from __future__ import annotations

import re
from html import unescape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scraper import Episode

_RUBY_RE = re.compile(r"<ruby>(.*?)</ruby>", re.S)
_RB_RE = re.compile(r"<rb>(.*?)</rb>", re.S)
_RT_RE = re.compile(r"<rt>(.*?)</rt>", re.S)
_RP_RE = re.compile(r"<rp>.*?</rp>", re.S)
_EM_BOUTEN_RE = re.compile(
    r'<em class="emphasisDots">(.*?)</em>', re.S
)
_IMG_RE = re.compile(r'<img src="([^"]*)" alt="([^"]*)"/>')
_BR_RE = re.compile(r"<br\s*/?>")
_TAG_RE = re.compile(r"<[^>]+>")

# 青空文庫の｜《》は原文中でも使われうるため、いわゆる「等号エスケープ」
# は行わない(narou.rb の html.rb も同様に無変換で出力している)。


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text)


def escape_literal_chuki_chars(text: str) -> str:
    """地の文由来の｜《》を、AozoraEpub3が解釈できる形でエスケープする

    直前に｢※｣を1つ置くことで「エスケープされた特殊文字」として扱われる
    (CharUtils#isEscapedChar の実装に基づく)。ruby_to_aozora を適用する
    *前*に呼び出すこと(このモジュールが後から生成する正規のルビ構文の
    ｜《》まで誤ってエスケープしてしまわないようにするため)。
    """
    return text.replace("｜", "※｜").replace("《", "※《").replace("》", "※》")


def ruby_to_aozora(text: str) -> str:
    """<ruby>...</ruby> を ｜base《reading》 形式に変換する

    html.rb の ruby_to_aozora(html.rb:71) に対応。<rb>が省略され
    テキストノードのみの場合にも対応する。
    """

    def repl(m: re.Match) -> str:
        inner = m.group(1)
        rt_match = _RT_RE.search(inner)
        if not rt_match:
            # <rt>が無い(壊れたruby)場合はルビ表記をあきらめて中身のみ出力
            return _strip_tags(inner)
        reading = _strip_tags(rt_match.group(1))
        base_part = inner[: rt_match.start()]
        base_part = _RP_RE.sub("", base_part)
        rb_match = _RB_RE.search(base_part)
        base = _strip_tags(rb_match.group(1)) if rb_match else _strip_tags(base_part)
        return f"｜{base}《{reading}》"

    return _RUBY_RE.sub(repl, text)


def em_to_aozora(text: str) -> str:
    """<em class="emphasisDots"> を ［＃傍点］...［＃傍点終わり］ に変換する

    html.rb の em_to_sesame(html.rb:107) に対応。
    """
    return _EM_BOUTEN_RE.sub(r"［＃傍点］\1［＃傍点終わり］", text)


def img_to_aozora(text: str, image_registry: dict[str, str]) -> str:
    """<img src="URL" alt="..."/> を ［＃挿絵（相対パス）入る］ に変換する

    html.rb の img_to_aozora(html.rb:97) に対応。ただし html.rb は
    URLをそのまま注記に埋め込むのに対し、narou_dlでは画像を事前に
    ローカル保存しておき、その相対パス(image_registry: url -> path)を
    埋め込む(narou.rb の illustration.rb の役割をnarou_dl側が代替する)。
    URLが registry に無い(ダウンロード失敗した)場合はタグごと削除する。
    """

    def repl(m: re.Match) -> str:
        rel_path = image_registry.get(m.group(1))
        return f"［＃挿絵（{rel_path}）入る］" if rel_path else ""

    return _IMG_RE.sub(repl, text)


def paragraph_to_aozora(text: str, image_registry: dict[str, str]) -> str:
    """段落1つ分のHTML断片(scraper.Episode.paragraphsの要素)を
    青空文庫記法の1行に変換する。空行(空文字列)はそのまま空行として返す。
    """
    if not text:
        return ""
    text = escape_literal_chuki_chars(text)  # 地の文の｜《》を先にエスケープ
    text = ruby_to_aozora(text)
    text = em_to_aozora(text)
    text = img_to_aozora(text, image_registry)
    text = _BR_RE.sub("\n", text)  # 段落内に<br/>が残っていれば改行として展開
    text = _strip_tags(text)  # <a>等、想定外に残ったタグを除去
    return unescape(text)


def chapter_heading(title: str) -> str:
    """章見出しの青空文庫注記を生成する

    converterbase.rb の enchant_midashi(converterbase.rb:1078) が
    最終的に生成する形式 "［＃３字下げ］［＃中見出し］タイトル［＃中見出し終わり］"
    に合わせている。見出しの前に改ページを入れることで、AozoraEpub3側が
    章単位でファイル分割・目次生成する挙動を誘発する。
    """
    title = escape_literal_chuki_chars(title)
    return f"［＃改ページ］\n［＃３字下げ］［＃中見出し］{title}［＃中見出し終わり］\n"


def episode_heading(subtitle: str) -> str:
    """話タイトルの青空文庫注記を生成する(章より1段階小さい見出し)"""
    subtitle = escape_literal_chuki_chars(subtitle)
    return f"［＃小見出し］{subtitle}［＃小見出し終わり］"


def build_novel_text(
    title: str,
    author: str,
    story: str,
    episodes: list["Episode"],
    chapter_map: dict[int, str],
    image_registry: dict[str, str],
) -> str:
    """作品全体を1つの青空文庫記法テキストにまとめる

    Downloader#sections_download_and_save(narou.rb) が話ごとにファイルを
    分けて保存するのに対し、AozoraEpub3は1テキストファイルの入力を前提と
    するため、narou_dlでは全話を連結した1ファイルを組み立てる。

    story(あらすじ)は、ebooklibバックエンドの_add_intro()が「あらすじ」
    専用ページを生成しているのと同じ情報を欠落させないために追加した
    (aozoraバックエンド側にこの処理が無く、あらすじが完全に失われて
    いた不具合の修正)。BookInfo(AozoraEpub3Converter.java)のタイトル/
    著者自動検出は「タイトル行、(空行)、著者名行、空行」という並びを
    前提にしており、あらすじをタイトル・著者のすぐ後ろに続けると
    自動検出が崩れるため、あらすじの前に見出し(［＃中見出し］あらすじ）
    を挟んで独立したセクションとして扱われるようにしている。
    """
    lines: list[str] = [
        escape_literal_chuki_chars(title),
        "",
        escape_literal_chuki_chars(author),
        "",
    ]
    if story.strip():
        lines.append("［＃中見出し］あらすじ［＃中見出し終わり］")
        for story_line in story.splitlines():
            lines.append(escape_literal_chuki_chars(story_line) if story_line else "")
        lines.append("［＃改ページ］")
    prev_chapter: str | None = None

    for ep in episodes:
        chapter = chapter_map.get(ep.index)
        if chapter and chapter != prev_chapter:
            lines.append(chapter_heading(chapter))
            prev_chapter = chapter

        lines.append(episode_heading(ep.subtitle))
        for paragraph in ep.paragraphs:
            lines.append(paragraph_to_aozora(paragraph, image_registry))

    return "\n".join(lines) + "\n"
