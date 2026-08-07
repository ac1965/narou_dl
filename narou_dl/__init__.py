"""narou_dl - 小説家になろう ダウンローダー & EPUB変換ツール

縦書き/横書き、章立て、ルビ、挿絵の埋め込み、ローカルキャッシュに対応。

変更履歴:
    v1.2.2
        - --aozoraepub3-jar 未指定時に環境変数 AOZORAEPUB3_JAR をデフォルト値
          として使えるようにした(毎回のパス指定を省略可能)。
    v1.2.1
        - --backend aozoraepub3 の出力先オプションのフラグ名を修正
          (誤: -dst / 正: -d, --dst)。実機のAozoraEpub3.jarで検証済み。
    v1.2.0
        - --backend {ebooklib,aozoraepub3} を追加。aozoraepub3 指定時は
          本文を青空文庫記法に変換し(aozora.py)、AozoraEpub3(改造版)の
          外部プロセス実行(aozoraepub3_backend.py)でEPUB化する。
          --aozoraepub3-jar / --device オプションを新設。
        - なろうの傍点表現(<em class="emphasisDots">)を保持するよう
          scraper.py を拡張。ebooklibバックエンドにも表示用CSSを追加。
    v1.1.0
        - 章立て(「第一章」などの区切り)への対応を追加 (scraper.fetch_chapter_map,
          epub_builder.build_epub の chapter_map 引数)
        - 本文中のルビ(<ruby>タグ)を保持してEPUBへ埋め込むよう対応
        - 本文中の挿絵(<img>タグ)を実データごとダウンロードしてEPUBへ同梱するよう対応
          (embed_images 引数、--no-images オプション)
        - ローカルキャッシュ機能を追加 (cache.Cache)。取得済みの作品メタデータ・
          本文・章立て・挿絵を保存し、同じ作品の再取得時にネットワークアクセスを
          省略できるようにした (--refresh / --clear-cache / --no-cache / --cache-dir)
    v1.0.0
        - 初版。なろう小説のダウンロードと縦書き/横書きEPUB変換の基本機能
          (--yoko オプションによる横書き切り替えを含む)
"""

__version__ = "1.2.2"
