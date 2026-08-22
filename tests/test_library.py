"""library.py の登録・削除・一覧読み込みに対するテスト。"""
from __future__ import annotations

from narou_dl.library import Library


def test_add_and_load_round_trip(tmp_path):
    library = Library(cache_dir=tmp_path)
    assert library.load() == {}

    library.add("N0000AA", "テスト作品", {"yoko": True, "sleep": 1.0})
    entries = library.load()

    assert set(entries.keys()) == {"n0000aa"}  # ncodeは小文字に正規化される
    entry = entries["n0000aa"]
    assert entry.title == "テスト作品"
    assert entry.options == {"yoko": True, "sleep": 1.0}
    assert entry.added_at  # 何らかの日時文字列が入っている


def test_add_overwrites_existing_entry(tmp_path):
    library = Library(cache_dir=tmp_path)
    library.add("n0000aa", "旧タイトル", {"yoko": False})
    library.add("n0000aa", "新タイトル", {"yoko": True})

    entries = library.load()
    assert len(entries) == 1
    assert entries["n0000aa"].title == "新タイトル"
    assert entries["n0000aa"].options == {"yoko": True}


def test_remove_returns_false_when_not_registered(tmp_path):
    library = Library(cache_dir=tmp_path)
    assert library.remove("n0000aa") is False


def test_remove_deletes_entry(tmp_path):
    library = Library(cache_dir=tmp_path)
    library.add("n0000aa", "テスト作品", {})

    assert library.remove("n0000aa") is True
    assert library.load() == {}


def test_load_returns_empty_dict_when_file_missing(tmp_path):
    library = Library(cache_dir=tmp_path / "does_not_exist")
    assert library.load() == {}


def test_load_returns_empty_dict_when_file_corrupted(tmp_path):
    library = Library(cache_dir=tmp_path)
    library.path.parent.mkdir(parents=True, exist_ok=True)
    library.path.write_text("not valid json", encoding="utf-8")

    assert library.load() == {}


def test_multiple_entries_are_independent(tmp_path):
    library = Library(cache_dir=tmp_path)
    library.add("n0000aa", "作品A", {})
    library.add("n0000bb", "作品B", {})

    entries = library.load()
    assert set(entries.keys()) == {"n0000aa", "n0000bb"}
