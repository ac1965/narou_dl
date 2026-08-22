"""cli.py の補助関数に対するテスト。"""
from __future__ import annotations

from narou_dl.cli import extract_ncode, sanitize_filename


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
