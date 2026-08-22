"""CLI・GUIで共有する既定オプション(config.json)。

これまでGUI(narou_dl.gui.main_window)は前回起動時のオプションを
QSettings(macOS固有のバイナリplist、~/Library/Preferences/配下)に
保存していたが、CLIには対応する永続化が無く、両者の既定値が食い違う
(例: GUIで選んだEPUB化バックエンドがCLI実行時には反映されない)構造に
なっていた。CLIとGUIが同じこのモジュールを読み書きすることで、
「設定は1箇所」を保証する(QSettingsのようなGUI専用ストアには依存しない)。

保存先は cache.py の XDG_CACHE_HOME方式に倣い、環境変数 XDG_CONFIG_HOME が
設定されていれば `$XDG_CONFIG_HOME/narou-dl/config.json`、なければ
`~/.config/narou-dl/config.json`。

CLI側は起動時にこのファイルの値をargparseの既定値として読み込み、
`--save-config` を指定すると現在指定したオプションをここに書き戻す。
GUI側は起動時に読み込んでUIへ反映し、ダウンロード開始時に書き戻す。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_KEYS = (
    "yoko",
    "no_chapters",
    "no_images",
    "sleep",
    "backend",
    "aozoraepub3_jar",
    "device",
    "emit_aozora_txt",
    "emit_aozora_txt_images",
)

DEFAULTS: dict = {
    "yoko": False,
    "no_chapters": False,
    "no_images": False,
    "sleep": 1.0,
    "backend": "ebooklib",
    "aozoraepub3_jar": None,
    "device": None,
    "emit_aozora_txt": False,
    "emit_aozora_txt_images": False,
}


def config_path() -> Path:
    """設定ファイルの保存先を決定する。

    Returns:
        `$XDG_CONFIG_HOME/narou-dl/config.json`(環境変数が設定されていれば)、
        なければ `~/.config/narou-dl/config.json`。
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "narou-dl" / "config.json"


def load_config() -> dict:
    """保存済みの既定オプションを読み込む。

    Returns:
        CONFIG_KEYSの全キーを持つ対応表。ファイルが無い、壊れている、
        または一部のキーが欠けている場合はDEFAULTSの値で補う。
    """
    path = config_path()
    merged = dict(DEFAULTS)
    if not path.exists():
        return merged
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return merged
    merged.update({k: data[k] for k in CONFIG_KEYS if k in data})
    return merged


def save_config(values: dict) -> None:
    """既定オプションを保存する。

    Args:
        values: 保存する値の対応表。CONFIG_KEYSに含まれないキーは無視する。
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: values[key] for key in CONFIG_KEYS if key in values}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
