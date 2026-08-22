"""cache.py のキャッシュ保存・読み込み・鮮度判定に対するテスト。"""
from __future__ import annotations

from narou_dl.api import NovelInfo
from narou_dl.cache import Cache
from narou_dl.scraper import Episode


def _info(ncode: str = "n0000aa") -> NovelInfo:
    return NovelInfo(
        ncode=ncode,
        title="テスト作品",
        writer="テスト作者",
        story="あらすじ",
        general_all_no=10,
        novel_type=1,
        end=0,
    )


def test_info_round_trip(tmp_path):
    cache = Cache("n0000aa", cache_dir=tmp_path)
    assert cache.load_info() is None

    cache.save_info(_info())
    loaded = cache.load_info()

    assert loaded is not None
    assert loaded.title == "テスト作品"
    assert loaded.writer == "テスト作者"


def test_episode_round_trip(tmp_path):
    cache = Cache("n0000aa", cache_dir=tmp_path)
    episode = Episode(index=1, subtitle="第一話", paragraphs=["本文1", "", "本文2"])

    assert cache.load_episode(1) is None

    cache.save_episode(episode, updated_at="2020/01/01 00:00")
    loaded = cache.load_episode(1)

    assert loaded is not None
    assert loaded.subtitle == "第一話"
    assert loaded.paragraphs == ["本文1", "", "本文2"]
    assert cache.load_episode_updated_at(1) == "2020/01/01 00:00"


def test_load_episode_updated_at_is_none_when_not_saved(tmp_path):
    cache = Cache("n0000aa", cache_dir=tmp_path)
    episode = Episode(index=1, subtitle="第一話", paragraphs=[])

    cache.save_episode(episode)  # updated_atを指定しない

    assert cache.load_episode_updated_at(1) is None


def test_chapter_map_invalidated_when_total_episodes_changes(tmp_path):
    cache = Cache("n0000aa", cache_dir=tmp_path)
    cache.save_chapter_map({1: "第一章", 2: "第一章"}, total_episodes=2)

    assert cache.load_chapter_map(total_episodes=2) == {1: "第一章", 2: "第一章"}
    # 新しい話が投稿されて全話数が変わった場合はキャッシュを無効とみなす
    assert cache.load_chapter_map(total_episodes=3) is None


def test_image_round_trip(tmp_path):
    cache = Cache("n0000aa", cache_dir=tmp_path)
    url = "https://example.com/img.png"

    assert cache.load_image(url) is None

    cache.save_image(url, b"\x89PNG...", "image/png")
    loaded = cache.load_image(url)

    assert loaded is not None
    content, content_type = loaded
    assert content == b"\x89PNG..."
    assert content_type == "image/png"


def test_clear_removes_all_cached_data(tmp_path):
    cache = Cache("n0000aa", cache_dir=tmp_path)
    cache.save_info(_info())
    cache.save_episode(Episode(index=1, subtitle="x", paragraphs=[]))

    assert cache.root.exists()
    cache.clear()
    assert not cache.root.exists()
    assert cache.load_info() is None


def test_ncode_is_case_normalized_to_lowercase(tmp_path):
    cache = Cache("N0000AA", cache_dir=tmp_path)
    assert cache.ncode == "n0000aa"
    assert cache.root == tmp_path / "n0000aa"
