"""config.py(CLI・GUI共有の既定オプション)に対するテスト。"""
from __future__ import annotations

from narou_dl.config import DEFAULTS, config_path, load_config, save_config


def test_config_path_uses_xdg_config_home_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "narou-dl" / "config.json"


def test_config_path_falls_back_to_home_dot_config(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert str(config_path()).endswith(".config/narou-dl/config.json")


def test_load_config_returns_defaults_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert load_config() == DEFAULTS


def test_save_and_load_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    save_config({"yoko": True, "sleep": 2.5, "backend": "aozoraepub3"})

    loaded = load_config()
    assert loaded["yoko"] is True
    assert loaded["sleep"] == 2.5
    assert loaded["backend"] == "aozoraepub3"
    # 保存しなかったキーは既定値のまま補われる
    assert loaded["no_chapters"] is False
    assert loaded["aozoraepub3_jar"] is None


def test_save_config_ignores_unknown_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    save_config({"yoko": True, "not_a_real_key": "should be dropped"})

    raw = (tmp_path / "narou-dl" / "config.json").read_text(encoding="utf-8")
    assert "not_a_real_key" not in raw


def test_load_config_returns_defaults_when_file_corrupted(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not valid json", encoding="utf-8")

    assert load_config() == DEFAULTS


def test_load_config_fills_missing_keys_from_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"yoko": true}', encoding="utf-8")

    loaded = load_config()
    assert loaded["yoko"] is True
    assert loaded["sleep"] == DEFAULTS["sleep"]
