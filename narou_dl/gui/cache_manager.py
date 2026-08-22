"""キャッシュ管理タブ。

cache.py が作品ごとに作る `<cache_dir>/<ncode>/` ディレクトリを一覧表示し、
個別削除・全削除・Finderで開く操作をGUIから行えるようにする。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..cache import default_cache_dir


@dataclass
class CacheEntry:
    ncode: str
    title: str
    writer: str
    episode_count: int
    size_bytes: int
    path: Path


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def _format_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def scan_cache(cache_dir: Path) -> list[CacheEntry]:
    """cache_dir 直下の各作品キャッシュを走査する。

    Args:
        cache_dir: キャッシュのルートディレクトリ。

    Returns:
        作品ごとのCacheEntryのリスト(ncode順)。
    """
    entries: list[CacheEntry] = []
    if not cache_dir.exists():
        return entries
    for child in sorted(cache_dir.iterdir()):
        if not child.is_dir():
            continue
        info_path = child / "info.json"
        title, writer = child.name, ""
        if info_path.exists():
            try:
                data = json.loads(info_path.read_text(encoding="utf-8"))
                title = data.get("title", child.name)
                writer = data.get("writer", "")
            except (json.JSONDecodeError, OSError):
                pass
        episodes_dir = child / "episodes"
        episode_count = len(list(episodes_dir.glob("*.json"))) if episodes_dir.exists() else 0
        entries.append(
            CacheEntry(
                ncode=child.name,
                title=title,
                writer=writer,
                episode_count=episode_count,
                size_bytes=_dir_size(child),
                path=child,
            )
        )
    return entries


class CacheManagerWidget(QWidget):
    """キャッシュ済み作品の一覧・削除を行うウィジェット。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[CacheEntry] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("キャッシュディレクトリ:"))
        self.dir_edit = QLineEdit(str(default_cache_dir()))
        dir_row.addWidget(self.dir_edit, 1)
        browse_btn = QPushButton("参照...")
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse_btn)
        layout.addLayout(dir_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ncode", "タイトル", "作者", "話数", "サイズ"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("更新")
        refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(refresh_btn)

        reveal_btn = QPushButton("Finderで開く")
        reveal_btn.clicked.connect(self._reveal_selected)
        btn_row.addWidget(reveal_btn)

        btn_row.addStretch(1)

        clear_selected_btn = QPushButton("選択項目を削除")
        clear_selected_btn.clicked.connect(self._clear_selected)
        btn_row.addWidget(clear_selected_btn)

        clear_all_btn = QPushButton("すべて削除")
        clear_all_btn.clicked.connect(self._clear_all)
        btn_row.addWidget(clear_all_btn)

        layout.addLayout(btn_row)

        self.summary_label = QLabel()
        layout.addWidget(self.summary_label)

    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "キャッシュディレクトリを選択", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)
            self.refresh()

    def cache_dir(self) -> Path:
        return Path(self.dir_edit.text()) if self.dir_edit.text() else default_cache_dir()

    def refresh(self) -> None:
        self._entries = scan_cache(self.cache_dir())
        self.table.setRowCount(len(self._entries))
        total_size = 0
        for row, entry in enumerate(self._entries):
            self.table.setItem(row, 0, QTableWidgetItem(entry.ncode))
            self.table.setItem(row, 1, QTableWidgetItem(entry.title))
            self.table.setItem(row, 2, QTableWidgetItem(entry.writer))
            self.table.setItem(row, 3, QTableWidgetItem(str(entry.episode_count)))
            self.table.setItem(row, 4, QTableWidgetItem(_format_size(entry.size_bytes)))
            total_size += entry.size_bytes
        self.summary_label.setText(
            f"{len(self._entries)}作品 / 合計 {_format_size(total_size)}"
        )

    def _selected_entries(self) -> list[CacheEntry]:
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        return [self._entries[r] for r in sorted(rows)]

    def _reveal_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            return
        if sys.platform == "darwin":
            subprocess.run(["open", str(entries[0].path)], check=False)
        else:
            QMessageBox.information(self, "未対応", "この機能はmacOSでのみ利用できます。")

    def _clear_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            return
        names = "\n".join(f"- {e.title} ({e.ncode})" for e in entries)
        if not self._confirm(f"以下のキャッシュを削除しますか?\n\n{names}"):
            return
        for entry in entries:
            shutil.rmtree(entry.path, ignore_errors=True)
        self.refresh()

    def _clear_all(self) -> None:
        if not self._entries:
            return
        if not self._confirm(f"{len(self._entries)}作品すべてのキャッシュを削除しますか?"):
            return
        for entry in self._entries:
            shutil.rmtree(entry.path, ignore_errors=True)
        self.refresh()

    def _confirm(self, message: str) -> bool:
        reply = QMessageBox.question(
            self,
            "確認",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes
