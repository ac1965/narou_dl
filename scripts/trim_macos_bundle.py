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
# Qt/lib以外でまるごと不要なQtリソースディレクトリ。
# "plugins"は含めない: setup.pyの"qt_plugins"オプションで作られるコピーが
# 常に別の場所(Contents/Resources/qt_plugins/)に retreat先を持つとは限らず、
# ビルドによってはPySide6/Qt/plugins/自体が platforms/libqcocoa.dylib 等の
# 唯一のコピー先になることを実機で確認した。ここを無条件削除すると
# アプリが起動できなくなる("Could not find the Qt platform plugin cocoa")
# ため、plugins配下は必要なサブディレクトリ(platforms/styles/imageformats)
# だけを残す形で個別に整理する(REMOVE_QT_PLUGIN_SUBDIRS参照)。
REMOVE_QT_SUBDIRS = {"qml", "translations", "metatypes", "libexec"}
# Qt/plugins配下で不要なサブディレクトリ(platforms/styles/imageformatsは
# setup.pyのqt_pluginsオプションと一致させて残す)
REMOVE_QT_PLUGIN_SUBDIRS = {
    "generic", "iconengines", "networkinformation", "position", "tls",
    "sqldrivers", "qmltooling", "sensors", "sensorgestures", "geoservices",
    "renderers", "sceneparsers", "texttospeech", "webview", "multimedia",
    "canbus", "gamepads", "printsupport", "designer", "renderplugins",
}


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

    plugins_dir = pyside_dir / "Qt" / "plugins"
    if plugins_dir.is_dir():
        for entry in plugins_dir.iterdir():
            if entry.name in REMOVE_QT_PLUGIN_SUBDIRS:
                remove(entry)

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
    """Qtプラグイン(libqcocoa.dylib等)のrpathを修正する。

    py2appのpyside6レシピは、pipのPySide6パッケージ内 `Qt/plugins/platforms/`
    (rpath `@loader_path/../../lib` = 元のQt/lib を指す想定)にあった
    プラグインをコピーする際、コピー先のディレクトリ構成が変わっていても
    rpathを書き換えない。ビルドによってコピー先が
    `Contents/Resources/qt_plugins/platforms/`(元より2階層浅い→rpathが
    実際のフレームワークを指さなくなる)だったり、PySide6パッケージ自身の
    `Qt/plugins/platforms/`(元と同じ深さ→rpathはそのまま有効)だったりする
    ことを実機で確認した(py2app 0.28.10 + PySide6 6.11時点、依存パッケージの
    組み合わせによって変わる模様)。どちらの場合でも安全なように、
    実行ファイルの位置を起点にした@executable_pathベースの正しいrpathを
    無条件で追加する(既に有効なrpathがあっても実害は無い)。
    """
    pyside_dirs = list(app_path.glob("Contents/Resources/lib/python*/PySide6"))
    if not pyside_dirs:
        return
    py_dir_name = pyside_dirs[0].parent.name  # 例: "python3.10"
    correct_rpath = f"@executable_path/../Resources/lib/{py_dir_name}/PySide6/Qt/lib"

    resources_dir = app_path / "Contents" / "Resources"
    cocoa_plugins = list(resources_dir.rglob("platforms/libqcocoa.dylib"))
    if not cocoa_plugins:
        return
    plugins_dir = cocoa_plugins[0].parent.parent  # .../platforms の親(imageformats等と同階層)
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


def _can_sign(path: Path) -> bool:
    result = subprocess.run(
        ["codesign", "--force", "--sign", "-", str(path)],
        capture_output=True,
    )
    return result.returncode == 0


def fix_corrupted_dylibs(app_path: Path) -> None:
    """py2appのコピー処理でリライトされ、codesignが扱えなくなったdylibを修復する。

    Pillow(reportlab/pdf extraの依存先)が同梱するliblzma.5.dylibのように、
    py2appがインストール名を書き換える際にMach-Oの内部構造(既存の
    LC_CODE_SIGNATUREのオフセット等)を壊してしまい、codesignは元より
    ("--remove-signature"すら"internal error"で失敗する)、対象を
    削除するとPillowのlibtiff経由の依存で`from PIL import Image`自体が
    ImportErrorになる、という事例を実機で確認した(削除では直せず、
    かといって直接re-signもできない)。

    このアプリはビルド時に同じ共有ライブラリを複数箇所(例:
    Contents/Frameworks/ と 各パッケージ内の.dylibs/)へ複製することが多く、
    経験上どちらか一方だけが壊れ、もう一方は正常にコピーされることが
    多い。そこで同じファイル名を持つdylib/soをバンドル全体から集め、
    署名できるもの(正常な複製)が1つでもあれば、それで壊れている方を
    上書きし、インストール名(-id)だけ元の値に戻してから署名し直す。
    """
    groups: dict[str, list[Path]] = {}
    for path in app_path.rglob("*"):
        if path.is_file() and (path.suffix == ".dylib" or path.suffix == ".so"):
            groups.setdefault(path.name, []).append(path)

    for name, paths in groups.items():
        if len(paths) < 2:
            continue
        broken = [p for p in paths if not _can_sign(p)]
        if not broken:
            continue
        healthy = [p for p in paths if p not in broken]
        if not healthy:
            print(f"  [警告] {name} の全コピーが破損しており自動修復できません: {paths}")
            continue
        source = healthy[0]
        for target in broken:
            original_id = subprocess.run(
                ["otool", "-D", str(target)], capture_output=True, text=True, check=True
            ).stdout.splitlines()[-1].strip()
            print(f"  {target} を {source} の内容で修復します(-id={original_id})")
            shutil.copyfile(source, target)
            subprocess.run(["install_name_tool", "-id", original_id, str(target)], check=True)
            subprocess.run(["codesign", "--force", "--sign", "-", str(target)], check=True)


def resign(app_path: Path) -> None:
    """.appバンドル全体にad-hoc署名を(再)付与する。

    --deep を付けると、バンドル内の全Mach-Oバイナリを個別に検証・再署名
    しようとする。しかしPillow(reportlab/pdf extraの依存先)が同梱する
    liblzma.5.dylibのように、py2appがコピー時にリライトした結果
    codesignの厳格な検証に通らないバイナリが1つでも混入していると、
    "main executable failed strict validation" で失敗することを実機で
    複数回確認した(Pillowが無いクリーンな環境でビルドすれば起きないが、
    pdf extraを既定で含めるようにした以上、常に再現しうる)。
    py2app自身の内部署名処理も実際には非再帰的(バンドル全体を1つの単位
    として、ネストしたバイナリ個々は検証せずリソースのハッシュだけで
    封印する)ため、それに倣って --deep を付けずに署名する。未署名のまま
    残るネストしたdylibはmacOSの自動ad-hoc署名機構が実行時に補うため、
    動作上の問題にはならない(codesign --verify --deep --strict による
    事後検証は別途通ることを確認済み)。
    """
    subprocess.run(
        ["codesign", "--force", "--sign", "-", str(app_path)],
        check=True,
    )


def main() -> None:
    app_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist/narou-dl.app")
    if not app_path.exists():
        raise SystemExit(f".appが見つかりません: {app_path}")

    before = human_size(app_path)
    trim(app_path)
    fix_plugin_rpaths(app_path)
    fix_corrupted_dylibs(app_path)
    resign(app_path)
    after = human_size(app_path)
    print(f"{app_path}: {before} -> {after}")


if __name__ == "__main__":
    main()
