# narou-dl

「小説家になろう」の作品をダウンロードして、EPUB(縦書き/横書き)に変換するPythonツール。

## セットアップ

このディレクトリ(`pyproject.toml`があるフォルダ)で`make setup-cli`を
実行すると、専用の仮想環境(`.venv/`)を作ってインストールします。

```bash
make setup-cli
.venv/bin/narou-dl N9669BK
```

Makefileを使わず直接インストールする場合は以下でも構いません。

```bash
pip install -e .
```

`narou-dl` コマンドが使えるようになります。

インストールせずに直接実行したい場合は依存パッケージのみ入れてください。

```bash
pip install requests beautifulsoup4 ebooklib
python -m narou_dl N9669BK
```

### Makefile

| コマンド | 内容 |
| --- | --- |
| `make setup-cli` | CLI(`narou-dl`)用の仮想環境 `.venv/` を作りインストールする |
| `make setup-gui` | GUI(`narou-dl-gui`)用の仮想環境 `.venv-gui/` を作りインストールする |
| `make run ARGS='N9669BK --yoko'` | `.venv/`のCLIを実行する |
| `make run-gui` | `.venv-gui/`のGUIを起動する |
| `make app` | macOS用 `dist/narou-dl.app` をビルドする(後述) |
| `make clean` | `__pycache__`・各種キャッシュディレクトリを削除する |
| `make distclean` | `clean`に加え仮想環境・ビルド成果物も含めて全て削除する |

CLIとGUIの仮想環境を分けているのは、GUIのみが必要とするPySide6
(数百MB)をCLI用環境に持ち込まないため。

## 使い方

```bash
# ncode を指定してフル取得(縦書き。作品タイトル.epub が出力される)
narou-dl N9669BK

# 横書きで生成
narou-dl N9669BK --yoko

# 出力ファイル名・待機秒数を指定
narou-dl N9669BK -o musyoku.epub --sleep 1.5

# 一部の話だけ取得(1〜20話)
narou-dl N9669BK --start 1 --end 20

# 章立てを無視してフラットな目次にする
narou-dl N9669BK --no-chapters

# 挿絵を埋め込まない
narou-dl N9669BK --no-images

# キャッシュを無視して取り直す(改稿などで本文が更新された場合)
narou-dl N9669BK --refresh

# この作品のキャッシュを削除してから取得する
narou-dl N9669BK --clear-cache

# キャッシュを使わない
narou-dl N9669BK --no-cache

# AozoraEpub3(改造版)を外部プロセスとして使い、より高度な組版でEPUB化する
# (傍点・外字自動判定・縦中横・画像回り込み等。JRE(Java 21以降推奨)と
#  AozoraEpub3.jar本体が別途必要)
narou-dl N9669BK --backend aozoraepub3 --aozoraepub3-jar /path/to/AozoraEpub3.jar
# --aozoraepub3-jar の代わりに環境変数でも指定できる
export AOZORAEPUB3_JAR=~/.local/share/aozoraepub3/AozoraEpub3.jar
narou-dl N9669BK --backend aozoraepub3

# ebooklibでEPUBを生成しつつ、同じ話データから青空文庫記法テキスト(.txt)も書き出す
# (挿絵注記は既定で含めない。--backend aozoraepub3 とは併用不可)
narou-dl N9669BK --emit-aozora-txt
# 挿絵をダウンロードして挿絵注記も含める場合
narou-dl N9669BK --emit-aozora-txt --emit-aozora-txt-images
```

ncode は作品URL (`https://ncode.syosetu.com/n9669bk/`) の `n9669bk` の部分です。
大文字・小文字どちらでも指定できます。

`-o` に `.epub` 拡張子を付けずに指定した場合は自動的に付与されます。

## GUIアプリ(macOS)

CLIと同じダウンロード処理をPySide6製のGUIから使えます。

```bash
make setup-gui
make run-gui
```

Makefileを使わない場合:

```bash
pip install -e ".[gui]"
narou-dl-gui
# またはインストールせずに
python -m narou_dl.gui
```

- **ダウンロードタブ**: ncodeと出力先、取得範囲(開始/終了話数・待機時間)、
  縦書き/横書きなどの主要オプションを指定して実行できます。進捗バーと
  ログ表示で取得状況を確認できます(内部の処理はCLIの`run()`と共通)。
- **キャッシュ管理タブ**: `cache.py` が作品ごとに作るキャッシュディレクトリを
  一覧表示し、作品単位での削除・全削除・Finderで開く操作ができます。

### .appバンドルのビルド(py2app)

`py2app`で`narou-dl.app`を作れる。PySide6は多数の未使用フレームワーク
(QtWebEngine等)を含むため、素の`py2app`ビルドは1GB超になる。
`scripts/trim_macos_bundle.py`でこのアプリが実際に使うQtモジュールだけに
絞り込み、Qtプラグインの参照先修正・再署名まで行うと約200MBになる。

汚染されていないクリーンな仮想環境(pandas等の無関係なパッケージが
入っていない環境)でビルドすることを強く推奨する。dev環境に大量の
パッケージが入っていると、py2appがそれらを巻き込んで検出してしまい、
サイズ増大や無関係なパッケージの署名エラーの原因になる。そのため
`make app`は専用の仮想環境`.venv-app-build/`を都度作り直してビルドする
(`.venv/`や`.venv-gui/`とは共有しない)。

```bash
make app
open dist/narou-dl.app
```

`make app`は内部で以下を行っている(直接実行したい場合の参考):

```bash
python3 -m venv .venv-app-build
.venv-app-build/bin/pip install .
.venv-app-build/bin/pip install "PySide6>=6.5" py2app

# setup.py はpy2app専用で、pyproject.tomlの[project]と併存すると
# py2appが依存関係(install_requires)を検出してエラーになるため、
# ビルド中だけ一時的にpyproject.tomlを退避する
mv pyproject.toml pyproject.toml.bak
.venv-app-build/bin/python setup.py py2app
mv pyproject.toml.bak pyproject.toml

.venv-app-build/bin/python scripts/trim_macos_bundle.py dist/narou-dl.app
```

生成物は`dist/narou-dl.app`(中間ファイルは`build/`)。`make distclean`で
`.venv-app-build/`ごと削除できる。

## 仕組み

1. **なろう小説API** (`narou_dl/api.py`) でタイトル・作者・話数などのメタデータを取得
2. **スクレイパー** (`narou_dl/scraper.py`)
   - 各話ページ (`ncode.syosetu.com/{ncode}/{話数}/`) から本文を取得(ルビ`<ruby>`・挿絵`<img>`タグは保持)
   - 目次ページ (`ncode.syosetu.com/{ncode}/`) から章立て(「第一章」などの区切り)を取得
3. **EPUB化バックエンド**(`--backend`で選択、既定は`ebooklib`)
   - `ebooklib`(既定・**EPUBビルダー** `narou_dl/epub_builder.py`): narou_dl自身がHTMLを直接EPUB化する
     - 縦書き(既定): `writing-mode: vertical-rl`、右→左ページ送り、数字の縦中横対応
     - 横書き(`--yoko`): 通常の横書きレイアウト
     - 章立てがある作品は章の区切りページを挿入し、目次(EPUB nav)も章でネストする
     - ルビはそのままEPUBへ埋め込まれ、対応リーダーでふりがなとして表示される
     - 挿絵(本文中の画像)は実データをダウンロードしてEPUB内に同梱する(`--no-images`で無効化可能)
     - `--emit-aozora-txt` を指定すると、EPUBと同じ話データから独立して
       (**青空文庫記法変換** `narou_dl/aozora.py`)青空文庫記法テキストも書き出せる
       (`--emit-aozora-txt-images`で挿絵注記も含められる、既定は含めない)
   - `aozoraepub3`: 本文を(**青空文庫記法変換** `narou_dl/aozora.py`)で青空文庫記法テキストに変換し、
     AozoraEpub3(改造版、`narou_dl/aozoraepub3_backend.py`)を外部プロセス起動してEPUB化する。
     傍点・外字自動判定・縦中横・画像の自動回転や余白除去等、より高度な組版が必要な場合に指定する。
     JRE(Java 21以降推奨)と`AozoraEpub3.jar`本体が別途必要(`--aozoraepub3-jar`または環境変数`AOZORAEPUB3_JAR`で指定)
4. **キャッシュ** (`narou_dl/cache.py`)
   - 取得した作品メタデータ・本文・章立て・挿絵をキャッシュディレクトリに保存
   - 同じ作品を再度ダウンロードする際は、キャッシュにある話・挿絵はネットワークアクセスなしで再利用する
   - 章立ては全話数が変わると自動的にキャッシュを無効化する(新しい話が投稿された場合など)
   - `--refresh` でキャッシュを無視して取り直し、`--clear-cache` でキャッシュを削除、`--no-cache` でキャッシュ自体を使わない
   - キャッシュディレクトリの決定順序:
     1. `--cache-dir` で明示指定した場所
     2. 環境変数 `XDG_CACHE_HOME` が設定されていれば `$XDG_CACHE_HOME/narou-dl` (例: `~/.cache/narou-dl`)
     3. どちらも無ければカレントディレクトリ(プロジェクト内)の `./.narou-dl-cache`

## 制限事項・今後の拡張余地

- サイトのHTML構造が変更されると `narou_dl/scraper.py` の修正が必要になります
- `--sleep` は既定1秒です。サーバー負荷軽減のため、大量取得時はこれより短くしないことを推奨します
- 章立てのない作品では自動的にフラットな目次になります(`--no-chapters` で明示的に無効化も可能)
- 挿絵のダウンロードに失敗した場合、そのページの画像は取り除かれ、処理は継続します
- キャッシュされた話の本文は、作者が「改稿」で内容を更新しても自動検知はしません。最新の内容が必要な場合は `--refresh` を使ってください

## 利用上の注意

小説家になろうの規約・著作権を尊重し、個人の読書目的での利用にとどめてください。
ダウンロードした作品を再配布することはできません。挿絵の著作権は投稿者(絵師)に帰属します。

## APIドキュメント(Sphinx)

各モジュールのdocstringはSphinx(napoleon拡張によるGoogleスタイル)で
そのままAPIリファレンスとしてビルドできます。

```bash
pip install -e ".[docs]"
sphinx-build -b html docs docs/_build
```

`docs/_build/index.html` をブラウザで開くと閲覧できます。

## ライセンス

個人利用を想定したツールです。ライセンスは利用者の環境に合わせて設定してください。
