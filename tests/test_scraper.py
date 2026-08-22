"""scraper.py のHTML解析ロジックに対するテスト。

なろうの実際のページへは通信せず、実際のHTML構造(docstring・コメントに
記載されているセレクタ)を模したミニマムな固定HTMLをレスポンスとして
差し替える(FakeSession)ことで、パース処理だけを検証する。
"""
from __future__ import annotations

from narou_dl.scraper import EpisodeScraper, TocEntry


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    """url -> HTML文字列 の対応表からレスポンスを返す偽のrequests.Session。"""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.headers: dict[str, str] = {}

    def get(self, url: str, timeout: float | None = None) -> FakeResponse:
        for key, html in self.pages.items():
            if key in url:
                return FakeResponse(html)
        raise AssertionError(f"想定外のURLへのアクセス: {url}")


EPISODE_HTML = """
<html><body>
<div class="js-novel-text p-novel__text p-novel__text--preface">
<p>前書きです(本文として拾われてはいけない)</p>
</div>
<h1 class="p-novel__title p-novel__title--rensai">第一話　目覚め</h1>
<div class="js-novel-text p-novel__text">
<p>本文の最初の行。</p>
<p><br/></p>
<p>ルビ付き<ruby>漢字<rp>（</rp><rt>かんじ</rt><rp>）</rp></ruby>のテスト。</p>
<p><a href="https://example.com/link"><img src="//example.com/img.png" alt="挿絵"/></a></p>
<p>傍点<em class="emphasisDots">強調</em>です。</p>
</div>
<div class="js-novel-text p-novel__text p-novel__text--afterword">
<p>あとがきです(本文として拾われてはいけない)</p>
</div>
</body></html>
"""


def _scraper(pages: dict[str, str]) -> EpisodeScraper:
    scraper = EpisodeScraper(session=FakeSession(pages))
    return scraper


def test_fetch_episode_extracts_only_honbun_not_preface_or_afterword():
    scraper = _scraper({"n0000aa/1/": EPISODE_HTML})
    episode = scraper.fetch_episode("n0000aa", 1)

    assert episode.index == 1
    assert episode.subtitle == "第一話　目覚め"
    assert "前書き" not in "".join(episode.paragraphs)
    assert "あとがき" not in "".join(episode.paragraphs)


def test_fetch_episode_preserves_ruby_and_normalizes_blank_lines():
    scraper = _scraper({"n0000aa/1/": EPISODE_HTML})
    episode = scraper.fetch_episode("n0000aa", 1)

    assert episode.paragraphs[0] == "本文の最初の行。"
    assert episode.paragraphs[1] == ""  # <p><br/></p> は空行として正規化される
    assert (
        episode.paragraphs[2]
        == "ルビ付き<ruby>漢字<rp>（</rp><rt>かんじ</rt><rp>）</rp></ruby>のテスト。"
    )


def test_fetch_episode_unwraps_link_and_absolutizes_image_url():
    scraper = _scraper({"n0000aa/1/": EPISODE_HTML})
    episode = scraper.fetch_episode("n0000aa", 1)

    # <a>は剥がされ、プロトコル相対URLは絶対URLになる
    assert episode.paragraphs[3] == '<img src="https://example.com/img.png" alt="挿絵"/>'


def test_fetch_episode_preserves_bouten_emphasis():
    scraper = _scraper({"n0000aa/1/": EPISODE_HTML})
    episode = scraper.fetch_episode("n0000aa", 1)

    assert episode.paragraphs[4] == '傍点<em class="emphasisDots">強調</em>です。'


TOC_HTML = """
<html><body>
<div class="p-eplist">
  <div class="p-eplist__chapter-title">第一章</div>
  <div class="p-eplist__sublist">
    <a class="p-eplist__subtitle" href="/n0000aa/1/">第一話</a>
    <div class="p-eplist__update">2020/01/01 12:00</div>
  </div>
  <div class="p-eplist__sublist">
    <a class="p-eplist__subtitle" href="/n0000aa/2/">第二話</a>
    <div class="p-eplist__update">2020/01/02 12:00<span title="2020/01/03 08:00 改稿">(改)</span></div>
  </div>
</div>
</body></html>
"""


def test_fetch_toc_groups_by_chapter_and_assigns_sequential_index():
    scraper = _scraper({"n0000aa/": TOC_HTML})
    toc = scraper.fetch_toc("n0000aa", total_episodes=2)

    assert set(toc.keys()) == {1, 2}
    assert toc[1].chapter_title == "第一章"
    assert toc[2].chapter_title == "第一章"  # 章タイトルdivが無い限り同じ章を継続


def test_fetch_toc_uses_revision_title_when_present():
    scraper = _scraper({"n0000aa/": TOC_HTML})
    toc = scraper.fetch_toc("n0000aa", total_episodes=2)

    # 改稿が無い話は表示テキストそのまま
    assert toc[1].updated_at == "2020/01/01 12:00"
    # 改稿がある話は span[title] の日時(接尾の「改稿」は除去)を優先する
    assert toc[2].updated_at == "2020/01/03 08:00"


def test_fetch_chapter_map_returns_only_chapter_titles():
    scraper = _scraper({"n0000aa/": TOC_HTML})
    chapter_map = scraper.fetch_chapter_map("n0000aa", total_episodes=2)

    assert chapter_map == {1: "第一章", 2: "第一章"}


def test_fetch_toc_returns_empty_dict_for_zero_episodes():
    scraper = _scraper({})
    assert scraper.fetch_toc("n0000aa", total_episodes=0) == {}


def test_toc_entry_is_plain_dataclass():
    entry = TocEntry(index=1, chapter_title=None, updated_at="2020/01/01 00:00")
    assert entry.index == 1
    assert entry.chapter_title is None
