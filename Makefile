# narou_dl 開発用Makefile
#
# CLI(narou-dl)とGUI(narou-dl-gui)は依存関係の大きさが異なる
# (GUIはPySide6を要する)ため、別々の仮想環境にセットアップする。
# macOS用.appバンドルのビルドも、開発環境に入っている無関係な
# パッケージ(pandas等)を巻き込まないよう専用のクリーンな仮想環境を使う
# (詳細はREADME.mdの「.appバンドルのビルド」を参照)。

PYTHON ?= python3

VENV_CLI := .venv
VENV_GUI := .venv-gui
VENV_APP := .venv-app-build

INSTALL_DIR ?= /Applications

.PHONY: help setup-cli setup-gui run run-gui app install-app uninstall-app test clean distclean

help:
	@echo "make setup-cli  : narou-dl(CLI)用の仮想環境 $(VENV_CLI) を作りインストールする"
	@echo "make setup-gui  : narou-dl-gui用の仮想環境 $(VENV_GUI) を作りインストールする"
	@echo "make run ARGS='N9669BK --yoko' : CLI版を実行する"
	@echo "make run-gui    : GUI版を起動する"
	@echo "make app        : macOS用 dist/narou-dl.app をビルドする"
	@echo "make install-app: dist/narou-dl.app を $(INSTALL_DIR) にインストールする(無ければ先にビルドする)"
	@echo "make uninstall-app: $(INSTALL_DIR)/narou-dl.app を削除する"
	@echo "make test       : pytestでテストスイートを実行する"
	@echo "make clean      : __pycache__・各種キャッシュディレクトリを削除する"
	@echo "make distclean  : clean に加えて仮想環境・ビルド成果物も全て削除する"

$(VENV_CLI)/bin/python:
	$(PYTHON) -m venv $(VENV_CLI)

$(VENV_GUI)/bin/python:
	$(PYTHON) -m venv $(VENV_GUI)

$(VENV_APP)/bin/python:
	$(PYTHON) -m venv $(VENV_APP)

setup-cli: $(VENV_CLI)/bin/python
	$(VENV_CLI)/bin/pip install --upgrade pip
	$(VENV_CLI)/bin/pip install -e .
	@echo "-> $(VENV_CLI)/bin/narou-dl"

setup-gui: $(VENV_GUI)/bin/python
	$(VENV_GUI)/bin/pip install --upgrade pip
	$(VENV_GUI)/bin/pip install -e ".[gui]"
	@echo "-> $(VENV_GUI)/bin/narou-dl-gui"

run: setup-cli
	$(VENV_CLI)/bin/narou-dl $(ARGS)

run-gui: setup-gui
	$(VENV_GUI)/bin/narou-dl-gui

# --- macOS .appバンドル(py2app) ---
#
# pyproject.tomlの[project.dependencies]がpy2appのinstall_requires
# チェックと衝突するため、ビルド中だけ一時的に退避する。ビルドの
# 成否に関わらず必ず元に戻す(1行にまとめて1つのシェルで実行する)。
app: $(VENV_APP)/bin/python
	$(VENV_APP)/bin/pip install --upgrade pip
	$(VENV_APP)/bin/pip install .
	$(VENV_APP)/bin/pip install "PySide6>=6.5" py2app
	rm -rf build dist
	mv pyproject.toml pyproject.toml.bak; \
	$(VENV_APP)/bin/python setup.py py2app; status=$$?; \
	mv pyproject.toml.bak pyproject.toml; \
	exit $$status
	$(VENV_APP)/bin/python scripts/trim_macos_bundle.py dist/narou-dl.app
	@echo "-> dist/narou-dl.app"

# dist/narou-dl.appが無ければ先にビルドしてから $(INSTALL_DIR) へ配置する。
# 既存のインストール済みバンドルへの上書きを避けるため一旦削除してから
# コピーする。ditto はcp -Rと異なりmacOSのバンドル(拡張属性・コード署名を
# 含む)をそのまま複製できるため、.appのコピーにはditto を使う。
install-app:
	@if [ ! -d dist/narou-dl.app ]; then \
		echo "dist/narou-dl.app が無いため先にビルドします..."; \
		$(MAKE) app; \
	fi
	rm -rf "$(INSTALL_DIR)/narou-dl.app"
	ditto dist/narou-dl.app "$(INSTALL_DIR)/narou-dl.app"
	@echo "-> $(INSTALL_DIR)/narou-dl.app"

uninstall-app:
	rm -rf "$(INSTALL_DIR)/narou-dl.app"
	@echo "-> $(INSTALL_DIR)/narou-dl.app を削除しました"

test: $(VENV_CLI)/bin/python
	$(VENV_CLI)/bin/pip install -e ".[dev]"
	$(VENV_CLI)/bin/pytest

clean:
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	find . -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache docs/_build

distclean: clean
	rm -rf $(VENV_CLI) $(VENV_GUI) $(VENV_APP)
	rm -rf build dist narou_dl.egg-info
	rm -rf .narou-dl-cache
