"""GUIアプリのエントリポイント。"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    # macOSアプリとしてのメタデータ(About/バンドル識別等)のために設定する。
    # オプションの永続化自体はQSettingsではなくnarou_dl.config(CLIと共有する
    # config.json)で行っている(main_window.py参照)。
    app.setOrganizationName("ty07")
    app.setOrganizationDomain("ty07.net")
    app.setApplicationName("narou-dl")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
