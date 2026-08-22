"""cli.py の補助関数に対するテスト。"""
from __future__ import annotations

import pytest

from narou_dl.cli import extract_ncode, run, sanitize_filename
from narou_dl.config import load_config


def test_extract_ncode_passes_through_plain_ncode():
    assert extract_ncode("N9669BK") == "N9669BK"
    assert extract_ncode("n9669bk") == "n9669bk"


def test_extract_ncode_from_work_url():
    assert extract_ncode("https://ncode.syosetu.com/n9669bk/") == "n9669bk"


def test_extract_ncode_from_work_url_without_trailing_slash():
    assert extract_ncode("https://ncode.syosetu.com/n9669bk") == "n9669bk"


def test_extract_ncode_from_episode_url_uses_first_segment():
    assert extract_ncode("https://ncode.syosetu.com/n9669bk/1/") == "n9669bk"


def test_extract_ncode_accepts_url_without_scheme():
    assert extract_ncode("ncode.syosetu.com/n9669bk") == "n9669bk"


def test_extract_ncode_accepts_http_scheme():
    assert extract_ncode("http://ncode.syosetu.com/n9669bk/") == "n9669bk"


def test_extract_ncode_strips_surrounding_whitespace():
    assert extract_ncode("  N9669BK  ") == "N9669BK"
    assert extract_ncode("  https://ncode.syosetu.com/n9669bk/  ") == "n9669bk"


def test_sanitize_filename_replaces_forbidden_characters():
    assert sanitize_filename('a/b:c*d?e"f<g>h|i') == "a_b_c_d_e_f_g_h_i"


def test_save_config_without_ncode_saves_and_exits_without_download(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    status = run(["--save-config", "--yoko", "--sleep", "2.0", "--backend", "ebooklib"])

    assert status == 0
    saved = load_config()
    assert saved["yoko"] is True
    assert saved["sleep"] == 2.0


def test_save_config_persists_across_separate_run_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    run(["--save-config", "--emit-aozora-txt"])
    # 別のrun()呼び出し(argparseの構築からやり直し)でも設定ファイル経由で
    # 値が引き継がれることを確認する(CLIプロセスをまたいだ永続化の再現)
    assert load_config()["emit_aozora_txt"] is True


def test_no_chapters_flag_saves_true(monkeypatch, tmp_path):
    """回帰テスト: argparse.BooleanOptionalActionは"--no-"で始まるフラグ名
    (--no-chapters)自体と組み合わせると、そのフラグ自身も自動生成される
    否定形(--no-no-chapters)も両方Falseと判定してしまい、Trueにする手段が
    無くなる不具合があった(実機で発覚)。store_true/store_falseを
    別オプション(--no-chapters/--chapters)に分ける方式で修正した。
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    run(["--save-config", "--no-chapters"])

    assert load_config()["no_chapters"] is True


def test_chapters_flag_overrides_saved_no_chapters_default(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    run(["--save-config", "--no-chapters"])
    assert load_config()["no_chapters"] is True

    # 既定値がTrueになっていても、--chaptersで今回だけFalseに戻せること
    run(["--save-config", "--chapters"])
    assert load_config()["no_chapters"] is False


def test_no_images_flag_saves_true(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    run(["--save-config", "--no-images"])

    assert load_config()["no_images"] is True


def test_images_flag_overrides_saved_no_images_default(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    run(["--save-config", "--no-images"])
    assert load_config()["no_images"] is True

    run(["--save-config", "--images"])
    assert load_config()["no_images"] is False


def test_missing_ncode_without_management_flags_is_a_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    # parser.error()はargparseの慣例通りSystemExit(2)を送出する
    with pytest.raises(SystemExit) as exc_info:
        run([])

    assert exc_info.value.code == 2
    assert "ncode" in capsys.readouterr().err
