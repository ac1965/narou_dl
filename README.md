# narou-dl

「小説家になろう」の作品をダウンロードして、EPUB(縦書き/横書き)に変換するPythonツール。

## セットアップ

このディレクトリ(`pyproject.toml`があるフォルダ)で以下を実行してください。

```bash
pip install -e .
```

`narou-dl` コマンドが使えるようになります。

インストールせずに直接実行したい場合は依存パッケージのみ入れてください。

```bash
pip install requests beautifulsoup4 ebooklib
python -m narou_dl N9669BK
```

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
```

ncode は作品URL (`https://ncode.syosetu.com/n9669bk/`) の `n9669bk` の部分です。
大文字・小文字どちらでも指定できます。

## 仕組み

1. **なろう小説API** (`narou_dl/api.py`) でタイトル・作者・話数などのメタデータを取得
2. **スクレイパー** (`narou_dl/scraper.py`)
   - 各話ページ (`ncode.syosetu.com/{ncode}/{話数}/`) から本文を取得(ルビ`<ruby>`・挿絵`<img>`タグは保持)
   - 目次ページ (`ncode.syosetu.com/{ncode}/`) から章立て(「第一章」などの区切り)を取得
3. **EPUBビルダー** (`narou_dl/epub_builder.py`) でEPUBを生成
   - 縦書き(既定): `writing-mode: vertical-rl`、右→左ページ送り、数字の縦中横対応
   - 横書き(`--yoko`): 通常の横書きレイアウト
   - 章立てがある作品は章の区切りページを挿入し、目次(EPUB nav)も章でネストする
   - ルビはそのままEPUBへ埋め込まれ、対応リーダーでふりがなとして表示される
   - 挿絵(本文中の画像)は実データをダウンロードしてEPUB内に同梱する(`--no-images`で無効化可能)

## 制限事項・今後の拡張余地

- サイトのHTML構造が変更されると `narou_dl/scraper.py` の修正が必要になります
- `--sleep` は既定1秒です。サーバー負荷軽減のため、大量取得時はこれより短くしないことを推奨します
- 章立てのない作品では自動的にフラットな目次になります(`--no-chapters` で明示的に無効化も可能)
- 挿絵のダウンロードに失敗した場合、そのページの画像は取り除かれ、処理は継続します

## 利用上の注意

小説家になろうの規約・著作権を尊重し、個人の読書目的での利用にとどめてください。
ダウンロードした作品を再配布することはできません。挿絵の著作権は投稿者(絵師)に帰属します。

## ライセンス

個人利用を想定したツールです。ライセンスは利用者の環境に合わせて設定してください。
