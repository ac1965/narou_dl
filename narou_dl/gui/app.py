"""GUIアプリのエントリポイント。"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    # QSettings()を引数無しで使う各所(main_window.pyのQSettings永続化)が
    # 保存先を一意に決められるよう、組織名/アプリ名をここで設定しておく。
    app.setOrganizationName("ty07")
    app.setOrganizationDomain("ty07.net")
    app.setApplicationName("narou-dl")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
