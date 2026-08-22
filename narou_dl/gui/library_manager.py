"""ライブラリ管理タブ。

CLIの --library-list/--library-remove/--update-all に相当する操作を
GUIから行えるようにする。登録(--library-add相当)はダウンロードタブの
「この作品をライブラリに登録する」チェックボックスで行う。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..library import Library, LibraryEntry
from .worker import DownloadOptions, DownloadWorker


def _options_to_download_options(ncode: str, options: dict) -> DownloadOptions:
    """library.jsonに保存された辞書からDownloadOptionsを組み立てる。

    cli.py の _update_all() が parser.parse_args() でNamespaceを再構築する
    のと同じ役割を、GUI側ではDownloadOptionsの生成という形で行う。
    """
    jar = options.get("aozoraepub3_jar")
    return DownloadOptions(
        ncode=ncode,
        output=options.get("output"),
        sleep=options.get("sleep", 1.0),
        yoko=options.get("yoko", False),
        no_chapters=options.get("no_chapters", False),
        no_images=options.get("no_images", False),
        no_update_check=options.get("no_update_check", False),
        backend=options.get("backend", "ebooklib"),
        aozoraepub3_jar=Path(jar) if jar else None,
        device=options.get("device"),
        emit_aozora_txt=options.get("emit_aozora_txt", False),
        emit_aozora_txt_images=options.get("emit_aozora_txt_images", False),
        library_add=False,  # 既に登録済みなので更新実行時に再登録はしない
    )


class LibraryManagerWidget(QWidget):
    """登録済み作品の一覧表示・削除・一括更新を行うウィジェット。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: DownloadWorker | None = None
        self._entries: dict[str, LibraryEntry] = {}
        self._queue: list[str] = []
        self._queue_total = 0
        self._queue_index = 0
        self._cancelled = False
        self._results: list[tuple[str, str, str]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ncode", "タイトル", "バックエンド", "登録日"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("更新")
        refresh_btn.clicked.connect(self.refresh)
        btn_row.addWidget(refresh_btn)

        self.remove_btn = QPushButton("選択項目を削除")
        self.remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(self.remove_btn)

        btn_row.addStretch(1)

        self.run_selected_btn = QPushButton("選択項目を更新")
        self.run_selected_btn.clicked.connect(self._run_selected)
        btn_row.addWidget(self.run_selected_btn)

        self.run_all_btn = QPushButton("すべて更新")
        self.run_all_btn.clicked.connect(self._run_all)
        btn_row.addWidget(self.run_all_btn)

        self.cancel_btn = QPushButton("キャンセル")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_clicked)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self.status_label = QLabel()
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(120)
        layout.addWidget(self.log_edit)

    def _append_log(self, message: str) -> None:
        self.log_edit.append(message)

    # --- 一覧表示・削除(--library-list/--library-remove相当) ---

    def refresh(self) -> None:
        self._entries = Library(cache_dir=None).load()
        rows = sorted(self._entries.values(), key=lambda e: e.title)
        self.table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            self.table.setItem(row, 0, QTableWidgetItem(entry.ncode))
            self.table.setItem(row, 1, QTableWidgetItem(entry.title))
            self.table.setItem(row, 2, QTableWidgetItem(entry.options.get("backend", "ebooklib")))
            self.table.setItem(row, 3, QTableWidgetItem(entry.added_at))

    def _selected_ncodes(self) -> list[str]:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        return [self.table.item(row, 0).text() for row in rows]

    def _remove_selected(self) -> None:
        ncodes = self._selected_ncodes()
        if not ncodes:
            return
        reply = QMessageBox.question(
            self,
            "確認",
            f"{len(ncodes)}件をライブラリから削除しますか?(ダウンロード済みのEPUB自体は削除されません)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        library = Library(cache_dir=None)
        for ncode in ncodes:
            library.remove(ncode)
        self.refresh()

    # --- 一括更新(--update-all相当) ---

    def _run_selected(self) -> None:
        ncodes = self._selected_ncodes()
        if not ncodes:
            QMessageBox.information(self, "選択なし", "更新する作品を一覧から選択してください。")
            return
        self._start_queue(ncodes)

    def _run_all(self) -> None:
        if not self._entries:
            QMessageBox.information(self, "登録なし", "ライブラリに登録された作品がありません。")
            return
        self._start_queue(list(self._entries.keys()))

    def _start_queue(self, ncodes: list[str]) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self._queue = list(ncodes)
        self._queue_total = len(ncodes)
        self._queue_index = 0
        self._cancelled = False
        self._results = []
        self.log_edit.clear()
        self.run_selected_btn.setEnabled(False)
        self.run_all_btn.setEnabled(False)
        self.remove_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setVisible(True)
        self._start_next()

    def _start_next(self) -> None:
        if self._cancelled or not self._queue:
            self._finish_queue()
            return

        ncode = self._queue.pop(0)
        self._queue_index += 1
        entry = self._entries.get(ncode)
        if entry is None:
            self._on_item_finished(ncode, "error", "ライブラリに見つかりません")
            return

        self.status_label.setText(f"更新中: {self._queue_index}/{self._queue_total}件目 {ncode}({entry.title})")
        self._append_log(f"\n=== [{self._queue_index}/{self._queue_total}] {entry.title} ({ncode}) ===")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)

        options = _options_to_download_options(ncode, entry.options)
        self.worker = DownloadWorker(options)
        self.worker.log.connect(self._append_log)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(lambda path, n=ncode: self._on_item_finished(n, "ok", path))
        self.worker.finished_error.connect(
            lambda message, n=ncode: self._on_item_finished(n, "error", message)
        )
        self.worker.finished_cancelled.connect(lambda n=ncode: self._on_item_finished(n, "cancelled", ""))
        self.worker.start()

    def _on_progress(self, current: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)

    def _on_item_finished(self, ncode: str, status: str, detail: str) -> None:
        self._results.append((ncode, status, detail))
        if status == "error":
            self._append_log(f"[エラー] {detail}")
        self._start_next()

    def _cancel_clicked(self) -> None:
        self._cancelled = True
        self.cancel_btn.setEnabled(False)
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self._append_log("キャンセルを要求しました(現在の話の取得完了後に停止します)。")

    def _finish_queue(self) -> None:
        self.run_selected_btn.setEnabled(True)
        self.run_all_btn.setEnabled(True)
        self.remove_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.status_label.setVisible(False)

        if not self._results:
            return
        ok = sum(1 for _, s, _ in self._results if s == "ok")
        errors = [n for n, s, _ in self._results if s == "error"]
        cancelled = sum(1 for _, s, _ in self._results if s == "cancelled")
        summary = f"更新完了: 成功{ok}件"
        if errors:
            summary += f" / 失敗{len(errors)}件({', '.join(errors)})"
        if cancelled:
            summary += f" / キャンセル{cancelled}件"
        self._append_log(f"\n{summary}")
        QMessageBox.information(self, "ライブラリ更新完了", summary)
