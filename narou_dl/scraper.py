"""ncode.syosetu.com から各話の本文・目次(章立て)を取得するスクレイパー。

サイト構造::

    連載本文: https://ncode.syosetu.com/{ncode}/{episode_no}/
    短編本文: https://ncode.syosetu.com/{ncode}/
    目次:     https://ncode.syosetu.com/{ncode}/  (2ページ目以降は ?p=2 ...)

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

追加時期::

    v1.0.0  本文取得(fetch_episode)の基本機能
    v1.1.0  本文中のルビ(<ruby>)・挿絵(<img>)タグの保持、
            章立て取得(fetch_chapter_map)を追加

修正履歴::

    前書き(class末尾 --preface)を持つ話で、本文ではなく前書きを誤って
    取得してしまう不具合を修正した(_find_honbun)。前書き・あとがきも
    共通の "p-novel__text" クラスを持つため、前書きが本文より先に
    出現する話では本文が空(または前書きの内容)として取得されていた。

    Ruby版 narou (https://github.com/whiteleaf7/narou) の
    webnovel/ncode.syosetu.com.yaml を参考に以下を追加:
      - fetch_toc(): 章立てに加えて話ごとの最終更新日時(改稿があれば
        その日時、無ければ初回掲載日時)も取得するようにした
        (fetch_chapter_map は fetch_toc の薄いラッパーとして存続)。
        この更新日時はキャッシュの鮮度判定(cache.py)に使われ、
        なろう側で本文が改稿された話だけを自動的に再取得できるようにする。
      - 目次の最終ページ番号を、決め打ちの ceil(総話数/100) ではなく
        ページャーの「最後へ」リンクから動的に検出するようにした
        (取得できない場合は従来通りの計算にフォールバックする)。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from html import escape
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from .api import USER_AGENT

BASE_URL = "https://ncode.syosetu.com"
EPISODES_PER_TOC_PAGE = 100
_LAST_PAGE_RE = re.compile(r"[?&]p=(\d+)")

# 本文中でそのまま残してよいインライン要素(ルビ表記に必要な最小限)
_INLINE_ALLOWED_TAGS = {"ruby", "rt", "rp"}
# 中身を素通りさせる(タグ自体は残さない)要素。挿絵は<a>でリンクされて
# いることが多いため、リンクは剥がして中身(<img>)だけを取り出す。
_INLINE_UNWRAP_TAGS = {"a"}


@dataclass
class Episode:
    """1話分の本文データ。"""

    index: int
    """1始まりの通し話数。"""
    subtitle: str
    """サブタイトル(話のタイトル)。"""
    paragraphs: list[str] = field(default_factory=list)
    """段落のHTML断片のリスト(空行は空文字列 "")。
    <ruby>(ルビ)・<img>(挿絵)タグを含む場合がある、安全な
    (エスケープ済みの)HTML文字列として保持する。"""


class ScrapeError(RuntimeError):
    """本文または目次ページのHTML構造が想定と異なり、解析できなかった場合の例外。"""


@dataclass
class TocEntry:
    """目次ページから取得した、話1つ分のメタデータ。"""

    index: int
    """1始まりの通し話数。"""
    chapter_title: str | None
    """所属する章のタイトル。章立てが無い作品では None。"""
    updated_at: str
    """最終更新日時の文字列表現(改稿があればその日時、無ければ初回掲載日時)。
    書式はなろうの表示をそのまま使う(例: "2012/11/22 17:00")。
    値そのものに意味を持たせず、前回取得時の値と文字列比較して変化の
    有無を判定する(キャッシュの鮮度判定)ためだけに用いる。"""


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
    """<p>タグ1つ分を安全なHTML文字列にする

    なろうの空行は <p><br/></p> のように「<br/>のみを含むp」で表現される
    ことがある。この場合、実際のテキストが無い(空行である)ことを示すため
    空文字列 "" を返す(呼び出し側は "" を空行として扱う)。
    """
    if not p_tag.get_text(strip=True):
        return ""
    return "".join(_serialize_inline(c) for c in p_tag.contents)


class EpisodeScraper:
    """なろうの本文ページ・目次ページを取得するスクレイパー本体"""

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        """
        Args:
            session: 使い回す requests.Session。省略時は新規作成する。
            timeout: リクエストのタイムアウト秒数。
        """
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.timeout = timeout

    def _episode_url(self, ncode: str, episode_no: int | None) -> str:
        ncode = ncode.lower()
        if episode_no is None:
            return f"{BASE_URL}/{ncode}/"
        return f"{BASE_URL}/{ncode}/{episode_no}/"

    @staticmethod
    def _find_honbun(soup: BeautifulSoup) -> Tag | None:
        """ページ内から前書き・あとがきを除いた「本文」divを探す

        なろうのページには前書き(class末尾 --preface)・本文・あとがき
        (class末尾 --afterword)の最大3つの js-novel-text.p-novel__text
        要素が同時に存在しうる。前書き・あとがきも "p-novel__text" クラス
        自体は共通して持つため、単純な `div.js-novel-text.p-novel__text`
        セレクタでは前書きが先にマッチしてしまい、本文が空だと誤認する
        (前書き・あとがきが無い話も多いため長らく気づかれなかった不具合)。
        ここではクラスが過不足なく {"js-novel-text", "p-novel__text"} と
        一致する要素(前書き・あとがきの修飾クラスを持たないもの)だけを
        本文として選ぶ。
        """
        for candidate in soup.select("div.js-novel-text.p-novel__text"):
            classes = set(candidate.get("class") or [])
            if classes == {"js-novel-text", "p-novel__text"}:
                return candidate
        return None

    def fetch_episode(self, ncode: str, episode_no: int | None) -> Episode:
        """1話分の本文を取得する(ルビ・挿絵タグを保持したまま返す)

        Args:
            ncode: 作品コード。
            episode_no: 話数(1始まり)。短編の場合は None を指定する。

        Returns:
            取得した話データ。

        Raises:
            ScrapeError: 本文要素が見つからない(サイト構造変更など)場合。
            requests.RequestException: 通信自体に失敗した場合。
        """
        url = self._episode_url(ncode, episode_no)
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        body = self._find_honbun(soup)
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
        内部的には fetch_toc() を呼び出す薄いラッパー。

        Args:
            ncode: 作品コード。
            total_episodes: 全話数(なろう小説APIの general_all_no)。

        Returns:
            話数から章タイトルへの対応表。章立てがなければ空の dict。

        Raises:
            ScrapeError: 目次要素が見つからない(サイト構造変更など)場合。
            requests.RequestException: 通信自体に失敗した場合。
        """
        toc = self.fetch_toc(ncode, total_episodes)
        return {i: e.chapter_title for i, e in toc.items() if e.chapter_title}

    @staticmethod
    def _detect_last_toc_page(soup: BeautifulSoup) -> int | None:
        """目次ページャーの「最後へ」リンクから最終ページ番号を検出する

        見つからない場合(目次が1ページのみで、ページャー自体が無い場合
        など)は None を返す。
        """
        last_link = soup.select_one("a.c-pager__item--last")
        if last_link is None or not last_link.get("href"):
            return None
        m = _LAST_PAGE_RE.search(last_link["href"])
        return int(m.group(1)) if m else None

    @staticmethod
    def _parse_updated_at(sublist_tag: Tag) -> str:
        """<div class="p-eplist__sublist"> 1つ分から最終更新日時を取り出す

        改稿がある場合は <span title="YYYY/MM/DD HH:MM 改稿"> の日時を、
        無ければ初回掲載日時のテキストをそのまま使う。
        """
        update_div = sublist_tag.select_one("div.p-eplist__update")
        if update_div is None:
            return ""
        revise_span = update_div.select_one("span[title]")
        if revise_span is not None and revise_span.get("title"):
            return revise_span["title"].replace("改稿", "").strip()
        return update_div.get_text(strip=True)

    def fetch_toc(self, ncode: str, total_episodes: int) -> dict[int, TocEntry]:
        """目次ページを全て取得し、話数(1始まり) -> TocEntry の対応表を作る

        章タイトルに加えて、話ごとの最終更新日時(改稿検知用)も取得する。

        Args:
            ncode: 作品コード。
            total_episodes: 全話数(なろう小説APIの general_all_no)。

        Returns:
            話数から TocEntry への対応表。

        Raises:
            ScrapeError: 目次要素が見つからない(サイト構造変更など)場合。
            requests.RequestException: 通信自体に失敗した場合。
        """
        ncode = ncode.lower()
        toc: dict[int, TocEntry] = {}
        if total_episodes <= 0:
            return toc

        episode_no = 0
        current_chapter: str | None = None
        fallback_total_pages = max(1, math.ceil(total_episodes / EPISODES_PER_TOC_PAGE))
        page = 1
        last_page: int | None = None

        while True:
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

            if page == 1:
                last_page = self._detect_last_toc_page(soup) or fallback_total_pages

            for child in eplist.children:
                if not isinstance(child, Tag):
                    continue
                classes = child.get("class") or []
                if "p-eplist__chapter-title" in classes:
                    current_chapter = child.get_text(strip=True)
                elif "p-eplist__sublist" in classes:
                    episode_no += 1
                    toc[episode_no] = TocEntry(
                        index=episode_no,
                        chapter_title=current_chapter,
                        updated_at=self._parse_updated_at(child),
                    )

            if page >= (last_page or fallback_total_pages):
                break
            page += 1

        return toc
