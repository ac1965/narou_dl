"""narou_dl GUIのメインウィンドウ。

「ダウンロード」タブと「キャッシュ管理」タブを持つ。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .cache_manager import CacheManagerWidget
from .worker import DownloadOptions, DownloadWorker


class DownloadTab(QWidget):
    """ncode入力〜EPUBダウンロードを行うタブ。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: DownloadWorker | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.ncode_edit = QLineEdit()
        self.ncode_edit.setPlaceholderText("例: N9669BK")
        form.addRow("ncode:", self.ncode_edit)

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("省略時は作品タイトルから自動生成")
        output_row.addWidget(self.output_edit)
        browse_btn = QPushButton("参照...")
        browse_btn.clicked.connect(self._browse_output)
        output_row.addWidget(browse_btn)
        form.addRow("出力ファイル:", output_row)

        layout.addLayout(form)

        range_group = QGroupBox("取得範囲")
        range_layout = QFormLayout(range_group)
        self.start_spin = QSpinBox()
        self.start_spin.setRange(1, 999999)
        self.start_spin.setValue(1)
        range_layout.addRow("開始話数:", self.start_spin)
        self.end_spin = QSpinBox()
        self.end_spin.setRange(0, 999999)
        self.end_spin.setSpecialValueText("最終話まで")
        self.end_spin.setValue(0)
        range_layout.addRow("終了話数:", self.end_spin)
        self.sleep_spin = QDoubleSpinBox()
        self.sleep_spin.setRange(0.0, 60.0)
        self.sleep_spin.setSingleStep(0.5)
        self.sleep_spin.setValue(1.0)
        self.sleep_spin.setSuffix(" 秒")
        range_layout.addRow("待機時間:", self.sleep_spin)
        layout.addWidget(range_group)

        opts_group = QGroupBox("オプション")
        opts_layout = QVBoxLayout(opts_group)
        self.yoko_check = QCheckBox("横書きで生成する(既定は縦書き)")
        self.no_chapters_check = QCheckBox("章立てを無視してフラットな目次にする")
        self.no_images_check = QCheckBox("挿絵を埋め込まない")
        self.no_cache_check = QCheckBox("キャッシュを使わない")
        self.refresh_check = QCheckBox("キャッシュを無視して取り直す")
        self.clear_cache_check = QCheckBox("この作品のキャッシュを削除してから取得する")
        for chk in (
            self.yoko_check,
            self.no_chapters_check,
            self.no_images_check,
            self.no_cache_check,
            self.refresh_check,
            self.clear_cache_check,
        ):
            opts_layout.addWidget(chk)
        layout.addWidget(opts_group)

        self.download_btn = QPushButton("ダウンロード開始")
        self.download_btn.clicked.connect(self._start_download)
        layout.addWidget(self.download_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        layout.addWidget(self.log_edit, 1)

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "出力先を選択", "", "EPUBファイル (*.epub)")
        if path:
            self.output_edit.setText(path)

    def _append_log(self, message: str) -> None:
        self.log_edit.append(message)

    def _on_progress(self, current: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)

    def _start_download(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        ncode = self.ncode_edit.text().strip()
        if not ncode:
            QMessageBox.warning(self, "入力エラー", "ncodeを入力してください。")
            return

        options = DownloadOptions(
            ncode=ncode,
            output=self.output_edit.text().strip() or None,
            sleep=self.sleep_spin.value(),
            start=self.start_spin.value(),
            end=self.end_spin.value() or None,
            yoko=self.yoko_check.isChecked(),
            no_chapters=self.no_chapters_check.isChecked(),
            no_images=self.no_images_check.isChecked(),
            no_cache=self.no_cache_check.isChecked(),
            refresh=self.refresh_check.isChecked(),
            clear_cache=self.clear_cache_check.isChecked(),
        )

        self.log_edit.clear()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.download_btn.setEnabled(False)
        self.download_btn.setText("ダウンロード中...")

        self.worker = DownloadWorker(options)
        self.worker.log.connect(self._append_log)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_finished_ok)
        self.worker.finished_error.connect(self._on_finished_error)
        self.worker.start()

    def _reset_button(self) -> None:
        self.download_btn.setEnabled(True)
        self.download_btn.setText("ダウンロード開始")

    def _on_finished_ok(self, output_path: str) -> None:
        self._reset_button()
        QMessageBox.information(self, "完了", f"EPUBを生成しました:\n{output_path}")

    def _on_finished_error(self, message: str) -> None:
        self._reset_button()
        self._append_log(f"[エラー] {message}")
        QMessageBox.critical(self, "エラー", message)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("narou-dl")
        self.resize(720, 640)

        tabs = QTabWidget()
        tabs.addTab(DownloadTab(), "ダウンロード")
        tabs.addTab(CacheManagerWidget(), "キャッシュ管理")
        self.setCentralWidget(tabs)
