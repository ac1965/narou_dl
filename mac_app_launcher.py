"""py2appでmacOS用.appバンドルをビルドする際のエントリスクリプト。

py2appは `python setup.py py2app` 実行時にこのファイルをトップレベル
スクリプトとして直接実行するため、narou_dl.gui.app内の相対importが
壊れないよう、パッケージ外のこの薄いラッパー経由でmain()を呼び出す。

QtCore.QLibraryInfoによるqt.conf自動検出(Contents/Resources/qt.conf)は
py2appが生成するアプリのプロセス起動経路では機能せず、Qtプラグインの
探索先が空文字列になり "Could not find the Qt platform plugin cocoa"で
起動に失敗することを確認したため、PySide6をimportする前に
QT_QPA_PLATFORM_PLUGIN_PATH/QT_PLUGIN_PATHを明示的に設定する。

Qtプラグイン(libqcocoa.dylib等)の実際のコピー先はpy2appのビルドごとに
変わりうることを実機で確認した(setup.pyの"qt_plugins"オプション指定は
同じでも、あるビルドでは独立した Contents/Resources/qt_plugins/ 配下に、
別のビルドではPySide6パッケージ自身のQt/plugins/配下にコピーされた)。
そのため固定パスを決め打ちせず、Resources配下を実際に探索して
platforms/libqcocoa.dylibを含むディレクトリを動的に見つける。
"""
import os
import sys
from pathlib import Path


def _find_qt_plugins_dir(resources_dir: Path) -> Path | None:
    for cocoa in resources_dir.rglob("platforms/libqcocoa.dylib"):
        return cocoa.parent.parent
    return None


_resources_dir = Path(sys.executable).resolve().parent.parent / "Resources"
_plugins_dir = _find_qt_plugins_dir(_resources_dir)
if _plugins_dir is not None:
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(_plugins_dir))
    os.environ.setdefault("QT_PLUGIN_PATH", str(_plugins_dir))

from narou_dl.gui.app import main

if __name__ == "__main__":
    main()
