"""narou_dl GUIのメインウィンドウ。

「ダウンロード」「キャッシュ管理」「ライブラリ」の3タブを持つ。
ダウンロードタブは複数ncode(1行1件)の一括ダウンロードに対応し、
実行中はキャンセルボタンで次の話の境界まで待って中断できる。
起動時に前回の主要オプション(縦横・バックエンド設定等)を
narou_dl.config(CLIと共有するconfig.json)から復元し、ダウンロード
開始時に保存する。以前はQSettings(macOS固有のplist)に保存していたが、
CLIとは別ストアになり設定が食い違っていたため、CLIと同じ
narou_dl.configを使うように変更した。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..cli import extract_ncode
from ..config import load_config, save_config
from .cache_manager import CacheManagerWidget
from .library_manager import LibraryManagerWidget
from .worker import DownloadOptions, DownloadWorker


class DownloadTab(QWidget):
    """ncode入力〜EPUBダウンロードを行うタブ。複数ncode入力時は順番に処理する。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: DownloadWorker | None = None
        self._queue: list[str] = []
        self._batch_total = 0
        self._batch_index = 0
        self._batch_cancelled = False
        self._results: list[tuple[str, str, str]] = []  # (ncode, status, detail)
        self._current_ncode: str | None = None
        self._current_title: str | None = None
        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.ncode_edit = QPlainTextEdit()
        self.ncode_edit.setPlaceholderText(
            "1行に1つのncode(例: N9669BK)または作品URLを入力。"
            "複数行入力すると順番に一括ダウンロードします"
        )
        self.ncode_edit.setFixedHeight(70)
        self.ncode_edit.textChanged.connect(self._update_output_enabled)
        form.addRow("ncode:", self.ncode_edit)

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("省略時は作品タイトルから自動生成(複数ncode指定時は常に自動生成)")
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
        self.library_add_check = QCheckBox(
            "この作品をライブラリに登録する(CLIの--update-allで追跡・一括更新できるようになる)"
        )
        self.emit_pdf_check = QCheckBox(
            "縦書きPDFも生成する(Pure Python製の独自組版、バックエンド問わず利用可)"
        )
        for chk in (
            self.yoko_check,
            self.no_chapters_check,
            self.no_images_check,
            self.no_cache_check,
            self.refresh_check,
            self.clear_cache_check,
            self.library_add_check,
            self.emit_pdf_check,
        ):
            opts_layout.addWidget(chk)
        layout.addWidget(opts_group)

        layout.addWidget(self._build_backend_group())

        btn_row = QHBoxLayout()
        self.download_btn = QPushButton("ダウンロード開始")
        self.download_btn.clicked.connect(self._start_download)
        btn_row.addWidget(self.download_btn)
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
        layout.addWidget(self.log_edit, 1)

    def _build_backend_group(self) -> QGroupBox:
        group = QGroupBox("EPUB化バックエンド")
        form = QFormLayout(group)

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["ebooklib", "aozoraepub3"])
        self.backend_combo.currentTextChanged.connect(self._update_backend_enabled)
        form.addRow("バックエンド:", self.backend_combo)

        jar_row = QHBoxLayout()
        self.jar_edit = QLineEdit()
        self.jar_edit.setPlaceholderText("AozoraEpub3(改造版)の.jarへのパス")
        jar_row.addWidget(self.jar_edit)
        self.jar_browse_btn = QPushButton("参照...")
        self.jar_browse_btn.clicked.connect(self._browse_jar)
        jar_row.addWidget(self.jar_browse_btn)
        form.addRow("AozoraEpub3.jar:", jar_row)

        self.device_edit = QLineEdit()
        self.device_edit.setPlaceholderText("例: kindle(空欄で未指定)")
        form.addRow("デバイス最適化:", self.device_edit)

        self.emit_aozora_txt_check = QCheckBox(
            "EPUBと共に青空文庫記法テキスト(.txt)も書き出す(ebooklibバックエンドのみ)"
        )
        self.emit_aozora_txt_check.toggled.connect(self._update_backend_enabled)
        form.addRow(self.emit_aozora_txt_check)

        self.emit_aozora_txt_images_check = QCheckBox(
            "上記テキストに挿絵をダウンロードして挿絵注記も含める"
        )
        form.addRow(self.emit_aozora_txt_images_check)

        self._update_backend_enabled()
        return group

    def _update_backend_enabled(self) -> None:
        is_aozoraepub3 = self.backend_combo.currentText() == "aozoraepub3"
        self.jar_edit.setEnabled(is_aozoraepub3)
        self.jar_browse_btn.setEnabled(is_aozoraepub3)
        self.device_edit.setEnabled(is_aozoraepub3)
        # --emit-aozora-txt は aozoraepub3 バックエンドとは併用不可(cli.pyと同じ制約)
        self.emit_aozora_txt_check.setEnabled(not is_aozoraepub3)
        self.emit_aozora_txt_images_check.setEnabled(
            not is_aozoraepub3 and self.emit_aozora_txt_check.isChecked()
        )

    def _update_output_enabled(self) -> None:
        multiple = len(self._ncodes()) > 1
        self.output_edit.setEnabled(not multiple)

    def _ncodes(self) -> list[str]:
        """ncode欄の各行を読み取り、URL入力ならncode部分を抽出して返す(重複除去済み)。"""
        seen: set[str] = set()
        result: list[str] = []
        for line in self.ncode_edit.toPlainText().splitlines():
            raw = line.strip()
            if not raw:
                continue
            ncode = extract_ncode(raw)
            if ncode.lower() in seen:
                continue
            seen.add(ncode.lower())
            result.append(ncode)
        return result

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "出力先を選択", "", "EPUBファイル (*.epub)")
        if path:
            self.output_edit.setText(path)

    def _browse_jar(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "AozoraEpub3.jarを選択", "", "jarファイル (*.jar)")
        if path:
            self.jar_edit.setText(path)

    def _append_log(self, message: str) -> None:
        self.log_edit.append(message)

    def _on_progress(self, current: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(current)

    # --- 設定の永続化(narou_dl.config、CLIと共有) ---

    def _load_settings(self) -> None:
        config = load_config()
        self.yoko_check.setChecked(bool(config["yoko"]))
        self.no_chapters_check.setChecked(bool(config["no_chapters"]))
        self.no_images_check.setChecked(bool(config["no_images"]))
        self.sleep_spin.setValue(float(config["sleep"]))
        self.backend_combo.setCurrentText(str(config["backend"]))
        self.jar_edit.setText(config["aozoraepub3_jar"] or "")
        self.device_edit.setText(config["device"] or "")
        self.emit_aozora_txt_check.setChecked(bool(config["emit_aozora_txt"]))
        self.emit_aozora_txt_images_check.setChecked(bool(config["emit_aozora_txt_images"]))
        self.emit_pdf_check.setChecked(bool(config["emit_pdf"]))
        self._update_backend_enabled()

    def _save_settings(self) -> None:
        save_config({
            "yoko": self.yoko_check.isChecked(),
            "no_chapters": self.no_chapters_check.isChecked(),
            "no_images": self.no_images_check.isChecked(),
            "sleep": self.sleep_spin.value(),
            "backend": self.backend_combo.currentText(),
            "aozoraepub3_jar": self.jar_edit.text().strip() or None,
            "device": self.device_edit.text().strip() or None,
            "emit_aozora_txt": self.emit_aozora_txt_check.isChecked(),
            "emit_aozora_txt_images": self.emit_aozora_txt_images_check.isChecked(),
            "emit_pdf": self.emit_pdf_check.isChecked(),
        })

    # --- ダウンロード開始・キューの進行 ---

    def _start_download(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        ncodes = self._ncodes()
        if not ncodes:
            QMessageBox.warning(self, "入力エラー", "ncodeを1つ以上入力してください。")
            return
        if self.backend_combo.currentText() == "aozoraepub3" and not self.jar_edit.text().strip():
            QMessageBox.warning(
                self, "入力エラー", "aozoraepub3バックエンドにはAozoraEpub3.jarの指定が必要です。"
            )
            return

        self._save_settings()

        self._queue = ncodes
        self._batch_total = len(ncodes)
        self._batch_index = 0
        self._batch_cancelled = False
        self._results = []

        self.log_edit.clear()
        self.download_btn.setEnabled(False)
        self.download_btn.setText("ダウンロード中...")
        self.cancel_btn.setEnabled(True)
        self.ncode_edit.setEnabled(False)
        self.output_edit.setEnabled(False)

        self._start_next_in_queue()

    def _current_options(self, ncode: str) -> DownloadOptions:
        single = self._batch_total == 1
        return DownloadOptions(
            ncode=ncode,
            output=(self.output_edit.text().strip() or None) if single else None,
            sleep=self.sleep_spin.value(),
            start=self.start_spin.value(),
            end=self.end_spin.value() or None,
            yoko=self.yoko_check.isChecked(),
            no_chapters=self.no_chapters_check.isChecked(),
            no_images=self.no_images_check.isChecked(),
            no_cache=self.no_cache_check.isChecked(),
            refresh=self.refresh_check.isChecked(),
            clear_cache=self.clear_cache_check.isChecked(),
            backend=self.backend_combo.currentText(),
            aozoraepub3_jar=(Path(self.jar_edit.text().strip()) if self.jar_edit.text().strip() else None),
            device=self.device_edit.text().strip() or None,
            emit_aozora_txt=self.emit_aozora_txt_check.isChecked(),
            emit_aozora_txt_images=self.emit_aozora_txt_images_check.isChecked(),
            library_add=self.library_add_check.isChecked(),
            emit_pdf=self.emit_pdf_check.isChecked(),
        )

    def _start_next_in_queue(self) -> None:
        if self._batch_cancelled or not self._queue:
            self._finish_batch()
            return

        ncode = self._queue.pop(0)
        self._current_ncode = ncode
        self._current_title = None
        self._batch_index += 1
        self.status_label.setVisible(True)
        self.status_label.setText(self._status_text(ncode))
        if self._batch_total > 1:
            self._append_log(f"\n=== [{self._batch_index}/{self._batch_total}] {ncode} ===")

        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)

        options = self._current_options(ncode)
        self.worker = DownloadWorker(options)
        self.worker.log.connect(self._append_log)
        self.worker.progress.connect(self._on_progress)
        self.worker.title_fetched.connect(lambda title, n=ncode: self._on_title_fetched(n, title))
        self.worker.finished_ok.connect(lambda path, n=ncode: self._on_item_finished(n, "ok", path))
        self.worker.finished_error.connect(
            lambda message, n=ncode: self._on_item_finished(n, "error", message)
        )
        self.worker.finished_cancelled.connect(lambda n=ncode: self._on_item_finished(n, "cancelled", ""))
        self.worker.start()

    def _status_text(self, ncode: str, title: str | None = None) -> str:
        target = ncode if not title else f"{ncode}({title})"
        if self._batch_total > 1:
            return f"全体の進捗: {self._batch_index}/{self._batch_total}件目 {target}"
        return f"ダウンロード中: {target}"

    def _on_title_fetched(self, ncode: str, title: str) -> None:
        if ncode != self._current_ncode:
            return  # 既にキャンセル・次の作品へ進んだ後に届いた古いシグナルは無視する
        self._current_title = title
        self.status_label.setText(self._status_text(ncode, title))

    def _on_item_finished(self, ncode: str, status: str, detail: str) -> None:
        self._results.append((ncode, status, detail))
        if status == "error":
            self._append_log(f"[エラー] {detail}")
        self._start_next_in_queue()

    @staticmethod
    def _reveal_in_finder(path: str) -> None:
        """生成されたEPUB(またはその出力フォルダ)をFinderで開く(macOSのみ)。

        ファイルパスなら"-R"でそのファイルを選択状態にして表示し、
        フォルダパス(一括ダウンロード時の出力先)ならフォルダ自体を開く。
        """
        if sys.platform != "darwin":
            return
        if Path(path).is_dir():
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["open", "-R", path], check=False)

    def _cancel_clicked(self) -> None:
        self._batch_cancelled = True
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("キャンセル中...")
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self._append_log("キャンセルを要求しました(現在の話の取得完了後に停止します)。")

    def _finish_batch(self) -> None:
        self.download_btn.setEnabled(True)
        self.download_btn.setText("ダウンロード開始")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("キャンセル")
        self.ncode_edit.setEnabled(True)
        self._update_output_enabled()
        self.status_label.setVisible(False)

        if not self._results:
            return  # 何も実行されないままキャンセルされた場合

        ok_paths = [detail for _, s, detail in self._results if s == "ok"]
        errors = [n for n, s, _ in self._results if s == "error"]
        cancelled = sum(1 for _, s, _ in self._results if s == "cancelled")

        if self._batch_total == 1:
            ncode, status, detail = self._results[0]
            if status == "ok":
                self._show_completion_dialog(
                    "完了", "EPUBを生成しました。", reveal_path=detail
                )
            elif status == "cancelled":
                self._append_log("キャンセルされました。")
            return  # errorの場合は_on_item_finishedで既にログ表示済み

        summary = f"完了: 成功{len(ok_paths)}件"
        if errors:
            summary += f" / 失敗{len(errors)}件({', '.join(errors)})"
        if cancelled:
            summary += f" / キャンセル{cancelled}件"
        self._append_log(f"\n{summary}")
        # 一括ダウンロードの出力先は全て同じフォルダ(自動命名時のカレント
        # ディレクトリ)なので、個々のファイルではなくフォルダを表示する
        reveal_dir = str(Path(ok_paths[0]).resolve().parent) if ok_paths else None
        self._show_completion_dialog("一括ダウンロード完了", summary, reveal_path=reveal_dir)

    def _show_completion_dialog(self, title: str, message: str, reveal_path: str | None) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(QMessageBox.Information)
        box.addButton(QMessageBox.Ok)
        reveal_btn = None
        if reveal_path and sys.platform == "darwin":
            reveal_btn = box.addButton("Finderで表示", QMessageBox.ActionRole)
        box.exec()
        if reveal_btn is not None and box.clickedButton() is reveal_btn:
            self._reveal_in_finder(reveal_path)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("narou-dl")
        self.resize(760, 760)

        tabs = QTabWidget()
        tabs.addTab(DownloadTab(), "ダウンロード")
        tabs.addTab(CacheManagerWidget(), "キャッシュ管理")
        tabs.addTab(LibraryManagerWidget(), "ライブラリ")
        # キャッシュ管理・ライブラリタブはウィジェット生成時に一度読み込むだけで、
        # 他のタブ(ダウンロード完了時のキャッシュ保存・ライブラリ登録)による
        # 変更を自動では検知しない。タブを表示するたびに再読み込みすることで、
        # 「ダウンロードタブで登録したのにライブラリタブに出ない」事態を防ぐ。
        tabs.currentChanged.connect(
            lambda index: getattr(tabs.widget(index), "refresh", lambda: None)()
        )
        self.setCentralWidget(tabs)
