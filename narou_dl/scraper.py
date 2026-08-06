"""ncode.syosetu.com から各話の本文・目次(章立て)を取得するスクレイパー。

サイト構造:
  - 連載本文: https://ncode.syosetu.com/{ncode}/{episode_no}/
  - 短編本文: https://ncode.syosetu.com/{ncode}/
  - 目次: https://ncode.syosetu.com/{ncode}/  (2ページ目以降は ?p=2 ...)

  本文は <div class="js-novel-text p-novel__text"> 内の <p> 要素。
  サブタイトルは <h1 class="p-novel__title p-novel__title--rensai">。
  ルビは <ruby>漢字<rp>（</rp><rt>かんじ</rt><rp>）</rp></ruby> の形。

  目次ページは <div class="p-eplist"> の直下に
  <div class="p-eplist__chapter-title">章タイトル</div> と
  <div class="p-eplist__sublist"><a class="p-eplist__subtitle">話タイトル</a>...</div>
  が出現順に並ぶ。話数(1始まり)と episode の URL 番号は一致する。
  1ページあたり最大100話。

サイトのHTML構造は予告なく変更される可能性があるため、要素が見つからない場合は
分かりやすい例外を投げるようにしている。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from html import escape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from .api import USER_AGENT

BASE_URL = "https://ncode.syosetu.com"
EPISODES_PER_TOC_PAGE = 100

# 本文中でそのまま残してよいインライン要素(ルビ表記に必要な最小限)
_INLINE_ALLOWED_TAGS = {"ruby", "rt", "rp"}
# 中身を素通りさせる(タグ自体は残さない)要素。挿絵は<a>でリンクされて
# いることが多いため、リンクは剥がして中身(<img>)だけを取り出す。
_INLINE_UNWRAP_TAGS = {"a"}


@dataclass
class Episode:
    index: int           # 1始まりの通し話数
    subtitle: str         # サブタイトル(話のタイトル)
    # 段落のHTML断片のリスト(空行は "")。
    # <ruby>タグを含む場合がある安全なHTML文字列(エスケープ済み)として保持する。
    paragraphs: list[str] = field(default_factory=list)


class ScrapeError(RuntimeError):
    pass


def _serialize_inline(node) -> str:
    """本文中のノードをルビ・挿絵を保持した安全なHTML文字列にする"""
    if isinstance(node, NavigableString):
        return escape(str(node))
    if isinstance(node, Tag):
        if node.name == "br":
            return "<br/>"
        if node.name == "img":
            src = node.get("src")
            if not src:
                return ""
            # プロトコル相対URL(//xxx.mitemin.net/...)を絶対URLにする
            abs_src = urljoin(BASE_URL, src)
            alt = node.get("alt", "挿絵")
            return f'<img src="{escape(abs_src, quote=True)}" alt="{escape(alt, quote=True)}"/>'
        if node.name in _INLINE_ALLOWED_TAGS:
            inner = "".join(_serialize_inline(c) for c in node.contents)
            return f"<{node.name}>{inner}</{node.name}>"
        if node.name in _INLINE_UNWRAP_TAGS:
            # <a>等はリンク自体を保持せず、中身(挿絵など)だけを取り出す
            return "".join(_serialize_inline(c) for c in node.contents)
        # 想定外のタグ(なろう側の仕様変更などに備え)はテキストのみ抽出する
        return escape(node.get_text())
    return ""


def _serialize_paragraph(p_tag: Tag) -> str:
    return "".join(_serialize_inline(c) for c in p_tag.contents)


class EpisodeScraper:
    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.timeout = timeout

    def _episode_url(self, ncode: str, episode_no: int | None) -> str:
        ncode = ncode.lower()
        if episode_no is None:
            return f"{BASE_URL}/{ncode}/"
        return f"{BASE_URL}/{ncode}/{episode_no}/"

    def fetch_episode(self, ncode: str, episode_no: int | None) -> Episode:
        """1話分の本文を取得する(ルビは<ruby>タグを保持したまま返す)

        Args:
            ncode: 作品コード
            episode_no: 話数(1始まり)。短編の場合は None を指定する。
        """
        url = self._episode_url(ncode, episode_no)
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        body = soup.select_one("div.js-novel-text.p-novel__text")
        if body is None:
            raise ScrapeError(
                f"本文要素が見つかりませんでした (url={url})。"
                " サイトのHTML構造が変更された可能性があります。"
            )

        title_tag = soup.select_one("h1.p-novel__title")
        subtitle = title_tag.get_text(strip=True) if title_tag else ""

        paragraphs = [_serialize_paragraph(p) for p in body.find_all("p")]

        return Episode(index=episode_no or 1, subtitle=subtitle, paragraphs=paragraphs)

    def fetch_chapter_map(self, ncode: str, total_episodes: int) -> dict[int, str]:
        """目次ページを全て取得し、話数(1始まり) -> 章タイトル の対応表を作る

        章立てのない(フラットな目次の)作品の場合は空の dict を返す。

        Args:
            ncode: 作品コード
            total_episodes: 全話数(なろう小説APIの general_all_no)
        """
        ncode = ncode.lower()
        chapter_map: dict[int, str] = {}
        if total_episodes <= 0:
            return chapter_map

        total_pages = max(1, math.ceil(total_episodes / EPISODES_PER_TOC_PAGE))
        episode_no = 0
        current_chapter: str | None = None

        for page in range(1, total_pages + 1):
            url = f"{BASE_URL}/{ncode}/" if page == 1 else f"{BASE_URL}/{ncode}/?p={page}"
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            eplist = soup.select_one("div.p-eplist")
            if eplist is None:
                raise ScrapeError(
                    f"目次要素が見つかりませんでした (url={url})。"
                    " サイトのHTML構造が変更された可能性があります。"
                )

            for child in eplist.children:
                if not isinstance(child, Tag):
                    continue
                classes = child.get("class") or []
                if "p-eplist__chapter-title" in classes:
                    current_chapter = child.get_text(strip=True)
                elif "p-eplist__sublist" in classes:
                    episode_no += 1
                    if current_chapter:
                        chapter_map[episode_no] = current_chapter

        return chapter_map
