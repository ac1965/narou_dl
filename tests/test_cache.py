"""cache.py のキャッシュ保存・読み込み・鮮度判定に対するテスト。"""
from __future__ import annotations

from narou_dl.api import NovelInfo
from narou_dl.cache import Cache, default_cache_dir
from narou_dl.scraper import Episode


def test_default_cache_dir_uses_xdg_cache_home_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_cache_dir() == tmp_path / "narou-dl"


def test_default_cache_dir_ignores_cwd_when_xdg_unset(monkeypatch, tmp_path):
    """CLI・GUI・.appバンドルで起動時cwdが異なっても同じキャッシュ場所になること。

    以前はcwd基準(./.narou-dl-cache)にフォールバックしており、py2appの
    .appバンドルは起動時にcwdをアプリ内部へ変更するため、CLIとGUIで
    キャッシュが別々の場所に分かれてしまう不具合があった。
    """
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    result_from_tmp = default_cache_dir()

    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)
    result_from_elsewhere = default_cache_dir()

    assert result_from_tmp == result_from_elsewhere
    assert str(result_from_tmp).endswith(".cache/narou-dl")


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
