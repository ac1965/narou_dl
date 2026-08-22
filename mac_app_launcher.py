"""py2appでmacOS用.appバンドルをビルドする際のエントリスクリプト。

py2appは `python setup.py py2app` 実行時にこのファイルをトップレベル
スクリプトとして直接実行するため、narou_dl.gui.app内の相対importが
壊れないよう、パッケージ外のこの薄いラッパー経由でmain()を呼び出す。

QtCore.QLibraryInfoによるqt.conf自動検出(Contents/Resources/qt.conf)は
py2appが生成するアプリのプロセス起動経路では機能せず、Qtプラグインの
探索先が空文字列になり "Could not find the Qt platform plugin cocoa"で
起動に失敗することを確認したため、PySide6をimportする前に
QT_QPA_PLATFORM_PLUGIN_PATH/QT_PLUGIN_PATHを明示的に設定する。
"""
import os
import sys
from pathlib import Path

_plugins_dir = Path(sys.executable).resolve().parent.parent / "Resources" / "qt_plugins"
if _plugins_dir.is_dir():
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(_plugins_dir))
    os.environ.setdefault("QT_PLUGIN_PATH", str(_plugins_dir))

from narou_dl.gui.app import main

if __name__ == "__main__":
    main()
