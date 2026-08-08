"""narou_dl - 小説家になろう ダウンローダー & EPUB変換ツール

縦書き/横書き、章立て、ルビ、挿絵の埋め込み、ローカルキャッシュに対応。

変更履歴:
    v1.4.0
        - 実機検証の結果、ebooklibとAozoraEpub3の品質差の大半は「同じ
          HTML/CSSの実装差」に起因することが判明したため、以下を
          ebooklibバックエンドにも反映し、両バックエンドの構成を揃えた:
            - EpubHtmlのdirection指定(dir="rtl"属性)を削除。dir属性は
              双方向テキスト(BiDi)制御用でありAozoraEpub3も使用しない。
              句点(。)がApple Books実機で行頭に孤立する不具合の原因
              だった(縦書き自体はwriting-mode: vertical-rlのみで実現)。
            - nav.xhtmlに専用CSS(list-style:none等)を追加し、目次の
              話数表示にリーダー側の自動採番が重複しないようにした。
            - タイトル・著者は book.set_title()/add_author() による
              OPFメタデータ(dc:title/dc:creator)としてのみ持たせ、
              視覚的なタイトルページとしては表示しないようにした
              (AozoraEpub3の実際の出力に合わせた)。
            - 目次(nav.xhtml)をspineから外し、視覚的な1ページ目として
              表示されないようにした(AozoraEpub3のOPFのspineにもnavは
              含まれていないことを確認済み。TOCボタンからは引き続き
              参照可能)。
            - 前書き(あらすじ)はaozoraepub3バックエンド側にも
              追加済み(v1.3.1の続きの修正、下記参照)。
          両バックエンドの品質差が実質的に解消されたため、--backend の
          既定値を再び ebooklib に戻した(v1.3.0で一時的にAOZORAEPUB3_JAR
          設定時はaozoraepub3を自動選択するようにしていたが撤回)。
          --backend aozoraepub3 は、より高度な組版(傍点・外字・
          縦中横・画像回り込み等)が必要な場合の明示的な選択肢として残す。
        - aozoraバックエンドの build_novel_text() に story(あらすじ)を
          追加。ebooklibバックエンドの_add_intro()相当の情報が
          aozoraepub3バックエンド側で完全に欠落していた不具合を修正。
    v1.3.1
        - ebooklibバックエンドのCSSに text-align-last: left を追加。
          登場人物紹介のようなリーダードット付き短文がjustifyで間延び
          して表示される不具合を修正(AozoraEpub3の電書協標準CSSに
          合わせた)。
    v1.3.0
        - --backend 未指定時、環境変数 AOZORAEPUB3_JAR(または --aozoraepub3-jar)
          が設定されていれば aozoraepub3 を自動選択するようにした。
          ebooklibは登場人物紹介のようなリーダードット付きリストの整形品質で
          劣ることが実データで確認されたため、jarが使える環境では明示指定
          なしでもaozoraepub3を優先する(--backend ebooklib で従来通り
          明示的にフォールバック可能)。
          ※ v1.4.0でこの自動選択ロジックは撤回し、既定は再びebooklibに戻した。
    v1.2.3
        - aozoraepub3バックエンドで、地の文の｜《》(なろう作品の装飾記号)を
          エスケープするよう aozora.py を修正。「無職転生」等、地の文に
          《》を使う作品でAozoraEpub3側の「ルビ開始文字無し」警告が
          出ていた問題を解消(実機検証済み)。
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

__version__ = "1.4.0"
