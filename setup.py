"""py2appでmacOS用 narou-dl.app を作るためのビルド設定。

通常の `pip install` / `pip install -e .` は pyproject.toml
(setuptoolsバックエンド)がそのまま使われる。py2appは
`python setup.py py2app` というsetup.py直接呼び出しの形でしか
動作しないため、このファイルを別途用意している。

使い方は README.md の「.appバンドルのビルド(py2app)」を参照。
ビルド後は必ず scripts/trim_macos_bundle.py を実行すること
(py2appは既定でPySide6パッケージの全ファイルを無条件にコピーするため、
そのままだと1GB超になり、Qtプラグインの参照パスも壊れていて起動できない)。

生成物は dist/narou-dl.app (と中間ファイルの build/) に出力される。
"""
from setuptools import setup

APP = ["mac_app_launcher.py"]
DATA_FILES: list[str] = []
OPTIONS = {
    # PySide6アプリはargv_emulationと相性が悪いため無効化する
    "argv_emulation": False,
    "packages": ["narou_dl"],
    # py2app同梱のpyside6.pyレシピ(modulegraphがPySide6を検出すると
    # 自動適用される)に、必要なQtプラグインをここで指定して取得させる。
    # 未使用モジュールの"excludes"指定は無効(py2appはPySide6パッケージの
    # 全ファイルを無条件にコピーするため効果がないことを確認済み)。
    # サイズ削減はビルド後にscripts/trim_macos_bundle.pyで行う。
    "qt_plugins": ["platforms/*", "styles/*", "imageformats/*"],
    "plist": {
        "CFBundleName": "narou-dl",
        "CFBundleDisplayName": "narou-dl",
        "CFBundleIdentifier": "net.ty07.narou-dl",
        "CFBundleShortVersionString": "1.4.0",
        "CFBundleVersion": "1.4.0",
        "NSHumanReadableCopyright": "",
        "NSHighResolutionCapable": True,
    },
}

setup(
    app=APP,
    name="narou-dl",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
    # py2appは依存関係を自前でバンドルするため install_requires を許可しない。
    # pyproject.toml の [project.dependencies] がそのまま渡ると
    # py2app.build_app が "install_requires is no longer supported" で
    # 失敗するため、ここで明示的に空にして上書きする。
    install_requires=[],
)
