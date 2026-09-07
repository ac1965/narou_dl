# AGENTS.md

「小説家になろう」の作品をダウンロードしてEPUB(縦書き/横書き)に変換するCLI/GUIツール。
CLI(`narou-dl`)とmacOS用GUI(`narou-dl-gui`, PySide6)は`narou_dl/`配下の同じコアロジックを共有する。
詳細な技術仕様・コマンド一覧・制限事項は [README.md](README.md) を必ず参照すること(本ファイルでは重複させない)。

## セットアップ・実行

```bash
make setup-cli          # CLI用venv(.venv/)を作成しインストール
.venv/bin/narou-dl N9669BK
make setup-gui          # GUI用venv(.venv-gui/)を作成しインストール(macOS専用)
make run-gui
make test               # pytest実行
```

その他のMakefileターゲット(`make app`/`make install`等)は [README.md](README.md#makefile) を参照。

## ディレクトリ構成

- `narou_dl/api.py` — なろう小説APIでのメタデータ取得
- `narou_dl/scraper.py` — 話ページ・目次ページのスクレイピング(なろうのHTML構造に依存、最も壊れやすい)
- `narou_dl/epub_builder.py` — 既定バックエンド(`ebooklib`)によるEPUB生成(縦書き/横書き、章立て、ルビ、挿絵)
- `narou_dl/aozoraepub3_backend.py` — 外部プロセス(AozoraEpub3.jar)を使う代替バックエンド
- `narou_dl/aozora.py` — 青空文庫記法変換
- `narou_dl/cache.py` — 取得済みメタデータ・本文・章立て・挿絵のキャッシュ管理
- `narou_dl/library.py` — 追跡作品の登録・一括更新(`<cache_dir>/library.json`)
- `narou_dl/config.py` — CLI/GUI共有の既定オプション(`~/.config/narou-dl/config.json`)
- `narou_dl/pdf_builder.py` — Chromium(Playwright)によるEPUB→PDF変換
- `narou_dl/cli.py` — CLIエントリポイント(`narou-dl`)
- `narou_dl/gui/` — PySide6製GUI(`narou-dl-gui`、macOS専用)
- `tests/` — `scraper.py`中心のユニットテスト。実際のなろうサイトへは通信せず、固定HTMLやtmp_pathで完結する
- `scripts/epub2pdf.py` — 任意のEPUBをPDFに変換する薄いCLIラッパー
- `scripts/trim_macos_bundle.py` — `.app`バンドルのサイズ削減・再署名(py2appビルド用)
- `docs/` — Sphinx(napoleon拡張)によるAPIリファレンスのソース。各モジュールのdocstringから生成される

## 実装時の注意

- `scraper.py`はなろうのHTML構造に依存しており、サイト側の変更で壊れやすい。変更時はテスト(`tests/test_scraper.py`)を固定HTMLで確認すること
- キャッシュされた話は改稿の自動検知をしない(`--refresh`で明示的に取り直す)。`cache.py`を触る際はこの前提を崩さないこと
- CLI・GUIは`config.json`を共有するため、片方だけに新しいオプションを追加する場合も`config.py`の読み書きの対称性を保つこと
- EPUB化バックエンドは`ebooklib`(既定)と`aozoraepub3`(外部プロセス)の二択で、両者は排他的な機能(`--emit-aozora-txt`等)を持つ。バックエンド追加・変更時はこの分岐を`cli.py`/`gui/`双方で確認すること
- PDF出力(`pdf_builder.py`)はEPUB自身のCSS(`writing-mode`等)から縦書き/横書き・判型を自動判定する。独自の組版ロジックを追加でハードコードしない
- `make test`はplaywrightパッケージのみを要求し、Chromium本体のインストールは強制しない。Chromium未インストール環境では該当テスト1件が自動スキップされる

## ドキュメント

- README.md — コマンド一覧・オプション詳細・仕組み・制限事項
- `docs/` — Sphinx APIリファレンス(`sphinx-build -b html docs docs/_build`でビルド)
