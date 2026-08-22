#!/usr/bin/env python3
"""py2appでビルドしたnarou-dl.appから未使用のQt関連ファイルを削除する。

このGUIアプリはQtCore/QtGui/QtWidgetsしか使わないが、py2appは
PySide6をパッケージとして同梱する際にpipホイールの中身
(QtWebEngine・Qt3D・QtQuick・QtMultimedia等、使っていない
フレームワーク一式やQt開発ツール)までまるごとコピーしてしまい、
.appが1GB超に膨れる(py2app 0.28時点の既知の制約。excludesで
モジュールを除外してもフレームワーク本体のコピーは避けられない)。

実際にバイナリレベルで必要なフレームワークはQtCore/QtGui/QtWidgets
のみであることをotool -Lで確認済みのため、それ以外を削除しても
アプリの動作に影響しない。削除後はディレクトリ構成が変わり
py2appが付与した署名が無効になるため、ad-hoc署名を再度付与する。

使い方::

    python setup.py py2app
    python scripts/trim_macos_bundle.py dist/narou-dl.app
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# QtCore/QtGui/QtWidgetsはこのアプリが直接使う。それ以外にQtDBus/QtNetwork/
# QtPrintSupport/QtSvg(Widgets)/QtXml/QtOpenGL(Widgets)/QtConcurrentも残す。
# これらはPythonコードから直接importしなくても、QtCoreがmacOS実行時に
# 内部的にdlopenすることがあり(例: QtDBus)、削除すると@rpath解決に失敗して
# システムにHomebrew等の別のQtインストールがあった場合にそちらへ
# フォールバックし、二重ロードで初期化に失敗する不具合を実際に確認した。
# いずれも数MB程度なので残しても全体サイズへの影響は軽微。
KEEP_FRAMEWORKS = {
    "QtCore.framework",
    "QtGui.framework",
    "QtWidgets.framework",
    "QtDBus.framework",
    "QtNetwork.framework",
    "QtPrintSupport.framework",
    "QtSvg.framework",
    "QtSvgWidgets.framework",
    "QtXml.framework",
    "QtOpenGL.framework",
    "QtOpenGLWidgets.framework",
    "QtConcurrent.framework",
}
KEEP_ABI3 = {f"{name.removesuffix('.framework')}.abi3.so" for name in KEEP_FRAMEWORKS}
# QtQml/QtQuick等の未使用機能向けの補助ライブラリ(削除しても実行時に参照されない)
REMOVE_DYLIBS = {"libpyside6qml.abi3.6.11.dylib"}
# Qt Designer/Assistant/Linguist等の開発者向けGUIツール一式
REMOVE_APP_BUNDLES_SUFFIX = ".app"
# Qt開発用CLIツール(実行時には不要)
REMOVE_TOOLS = {
    "balsam", "balsamui", "lrelease", "lupdate",
    "qmlformat", "qmllint", "qmlls", "qsb", "svgtoqml",
}
# ソース同梱物(型スタブ・ドキュメント・ビルド用ヘッダ等、実行時には不要)
REMOVE_DIRS_IN_PYSIDE6 = {"doc", "include", "glue", "typesystems", "scripts", "QtAsyncio"}
# Qt/lib以外でまるごと不要なQtリソースディレクトリ
REMOVE_QT_SUBDIRS = {"qml", "translations", "metatypes", "plugins", "libexec"}


def human_size(path: Path) -> str:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    size = float(total)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def remove(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def trim(app_path: Path) -> None:
    pyside_dirs = list(app_path.glob("Contents/Resources/lib/python*/PySide6"))
    if not pyside_dirs:
        raise SystemExit(f"PySide6ディレクトリが見つかりません: {app_path}")
    pyside_dir = pyside_dirs[0]

    qt_lib_dir = pyside_dir / "Qt" / "lib"
    for framework in qt_lib_dir.glob("*.framework"):
        if framework.name not in KEEP_FRAMEWORKS:
            remove(framework)

    for name in REMOVE_QT_SUBDIRS:
        remove(pyside_dir / "Qt" / name)

    for so_file in pyside_dir.glob("*.abi3.so"):
        if so_file.name not in KEEP_ABI3:
            remove(so_file)
            remove(so_file.with_suffix("").with_suffix(".pyi"))  # 対応する.pyiも削除

    for name in REMOVE_DYLIBS:
        remove(pyside_dir / name)

    for entry in pyside_dir.iterdir():
        if entry.suffix == REMOVE_APP_BUNDLES_SUFFIX:
            remove(entry)
        elif entry.name in REMOVE_TOOLS:
            remove(entry)
        elif entry.name in REMOVE_DIRS_IN_PYSIDE6:
            remove(entry)


def fix_plugin_rpaths(app_path: Path) -> None:
    """qt_plugins配下のプラグイン(libqcocoa.dylib等)のrpathを修正する。

    py2appのpyside6レシピは、pipのPySide6パッケージ内 `Qt/plugins/platforms/`
    (rpath `@loader_path/../../lib` = 元のQt/lib を指す想定)にあった
    プラグインを、そのまま `Contents/Resources/qt_plugins/platforms/` へ
    コピーするだけでrpathを書き換えない。ディレクトリの深さが変わるため、
    コピー後は同じ相対rpathが実際のフレームワーク(Contents/Resources/
    lib/pythonX.Y/PySide6/Qt/lib)を指さなくなり、
    "Could not find the Qt platform plugin 'cocoa'" で起動に失敗する
    (py2app 0.28.10 + PySide6 6.11時点で確認済みの制約)。
    実行ファイルの位置は変わらないため、@executable_path起点の
    正しいrpathを追加で埋め込むことで解決する。
    """
    pyside_dirs = list(app_path.glob("Contents/Resources/lib/python*/PySide6"))
    if not pyside_dirs:
        return
    py_dir_name = pyside_dirs[0].parent.name  # 例: "python3.10"
    correct_rpath = f"@executable_path/../Resources/lib/{py_dir_name}/PySide6/Qt/lib"

    plugins_dir = app_path / "Contents" / "Resources" / "qt_plugins"
    for dylib in plugins_dir.rglob("*.dylib"):
        subprocess.run(
            ["install_name_tool", "-add_rpath", correct_rpath, str(dylib)],
            check=False,  # 既に同じrpathがあると失敗するが実害はないため無視する
        )
        # install_name_toolでの書き換えは既存の署名を壊れた状態のまま残す
        # (削除はしない)。最終的なアプリ全体のcodesignはbundle直下の
        # 再封印のみでネストしたdylib個々までは再署名しないため、この
        # まま放置するとdyldが「壊れた署名」とみなしSIGKILLで起動に
        # 失敗する(署名が全く無い場合はmacOSが自動でad-hoc署名を
        # 補うため問題にならないが、壊れた署名は補われない)。
        # そのためここで都度ad-hoc署名を上書きし直す。
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(dylib)],
            check=True,
        )


def resign(app_path: Path) -> None:
    subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", str(app_path)],
        check=True,
    )


def main() -> None:
    app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist/narou-dl.app")
    if not app_path.exists():
        raise SystemExit(f".appが見つかりません: {app_path}")

    before = human_size(app_path)
    trim(app_path)
    fix_plugin_rpaths(app_path)
    resign(app_path)
    after = human_size(app_path)
    print(f"{app_path}: {before} -> {after}")


if __name__ == "__main__":
    main()
