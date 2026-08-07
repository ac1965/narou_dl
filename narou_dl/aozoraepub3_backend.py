"""aozora.py で生成した青空文庫記法テキストを、AozoraEpub3(改造版)の
外部プロセス呼び出しでEPUB化するバックエンド。

Ruby版 narou の lib/novelconverter.rb の self.txt_to_epub
(novelconverter.rb:139-243) を Python に移植したもの。
以下、narou.rb実装から踏襲した(移植元コメントに明記されていた)注意点:

    1. AozoraEpub3は .jar のあるディレクトリがカレントディレクトリで
       ないと正しく動作しない。そのため subprocess の cwd を明示的に
       jarのあるディレクトリに設定する(novelconverter.rb:176 Dir.chdir)。
    2. AozoraEpub3はエラーが発生してもプロセスのexit codeは常に0を返す。
       失敗判定はstdoutのテキストパターン("[ERROR]"行の有無、
       "変換完了"という完了マーカーの有無)で行う必要がある
       (novelconverter.rb:193-236)。exit codeが非0になるのはJava自体が
       起動できない場合のみ。
    3. 文字コード関連のJVMオプション(-Dfile.encoding=UTF-8等)を
       付与しないと、環境によって日本語ファイル名/ログが化ける
       (novelconverter.rb:169-170)。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class AozoraEpub3Error(RuntimeError):
    """AozoraEpub3の実行に失敗した場合の例外。"""


class AozoraEpub3NotFound(AozoraEpub3Error):
    """指定された .jar が見つからない場合の例外。"""


_JAVA_ENCODING_OPTS = [
    "-Dfile.encoding=UTF-8",
    "-Dstdout.encoding=UTF-8",
    "-Dstderr.encoding=UTF-8",
    "-Dsun.stdout.encoding=UTF-8",
    "-Dsun.stderr.encoding=UTF-8",
]


@dataclass
class AozoraEpub3Result:
    epub_path: Path
    stdout: str
    warnings: list[str]


def _build_command(
    jar_name: str,
    src_path: Path,
    dst_dir: Path,
    vertical: bool,
    cover_first_image: bool,
    device: str | None,
) -> list[str]:
    cmd = ["java", *_JAVA_ENCODING_OPTS, "-cp", jar_name, "AozoraEpub3",
           "-enc", "UTF-8", "-of"]

    if device == "kindle":
        cmd += ["-device", "kindle"]

    if cover_first_image:
        # 先頭の挿絵注記を表紙として使う(novelconverter.rb:135 cover_option)
        cmd += ["-c", "0"]

    cmd += ["-dst", str(dst_dir.resolve())]

    if not vertical:
        cmd.append("-hor")  # yokogaki_option(novelconverter.rb:151)

    cmd.append(str(src_path.resolve()))
    return cmd


def build_epub_via_aozoraepub3(
    novel_txt_path: Path,
    aozoraepub3_jar: Path,
    dst_dir: Path,
    *,
    vertical: bool = True,
    cover_first_image: bool = True,
    device: str | None = None,
    verbose: bool = False,
) -> AozoraEpub3Result:
    """青空文庫記法テキストをAozoraEpub3で変換する

    Args:
        novel_txt_path: aozora.build_novel_text() で生成したテキストファイル。
        aozoraepub3_jar: AozoraEpub3.jar(改造版)への絶対パス。
        dst_dir: EPUB出力先ディレクトリ。
        vertical: 縦書きにするかどうか(既定True)。
        cover_first_image: 先頭挿絵を表紙にするかどうか。
        device: "kindle" 等、AozoraEpub3のデバイス最適化オプション。
        verbose: AozoraEpub3の標準出力をそのまま表示するかどうか。

    Returns:
        生成されたEPUBのパス等を含む結果オブジェクト。

    Raises:
        AozoraEpub3NotFound: jarファイルが存在しない場合。
        AozoraEpub3Error: 変換に失敗した場合(Java未インストール、
            AozoraEpub3自体のエラー、EPUB未生成など)。
    """
    if not aozoraepub3_jar.exists():
        raise AozoraEpub3NotFound(
            f"AozoraEpub3.jarが見つかりません: {aozoraepub3_jar}\n"
            "https://github.com/hmdev/AozoraEpub3 等から入手し、"
            "--aozoraepub3-jar で指定してください。"
        )
    dst_dir.mkdir(parents=True, exist_ok=True)

    jar_dir = aozoraepub3_jar.parent
    cmd = _build_command(
        aozoraepub3_jar.name, novel_txt_path, dst_dir,
        vertical, cover_first_image, device,
    )

    try:
        proc = subprocess.run(
            cmd, cwd=jar_dir, capture_output=True, text=True, timeout=300,
        )
    except FileNotFoundError as e:
        raise AozoraEpub3Error(
            "javaコマンドが見つかりません。JRE(Java 21以降を推奨)を"
            "インストールしてください。"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise AozoraEpub3Error("AozoraEpub3の実行がタイムアウトしました") from e

    # exit codeが非0 = Java自体が実行できなかった場合のみ
    # (novelconverter.rb:193 のコメント通り、AozoraEpub3自身のエラーは
    #  exit code 0 のまま stdout にエラーメッセージが出力される)
    if proc.returncode != 0:
        raise AozoraEpub3Error(
            f"Javaの実行に失敗しました(exit={proc.returncode}):\n{proc.stderr}"
        )

    stdout = proc.stdout

    if "Error occurred during initialization of VM" in stdout:
        raise AozoraEpub3Error(
            f"Javaの実行環境エラーが発生しました。複数のJava環境が混在して"
            f"いないか確認してください:\n{stdout.strip()}"
        )

    error_lines = [
        line for line in stdout.splitlines()
        if line.startswith("[ERROR]") or "エラーが発生しました :" in line
    ]
    warn_lines = [line for line in stdout.splitlines() if line.startswith("[WARN]")]

    if verbose:
        print("==== AozoraEpub3 stdout ====")
        print(stdout.strip())
        print("=" * 40)

    if error_lines and "変換完了" not in stdout:
        # EPUBが出力されないタイプのエラー
        # (novelconverter.rb:231 のコメント通り、"変換完了"の有無で
        #  EPUBが出力されるエラーかどうかを判定する)
        raise AozoraEpub3Error(
            "AozoraEpub3実行中にエラーが発生し、EPUBが出力されませんでした:\n"
            + "\n".join(error_lines)
        )

    produced = sorted(dst_dir.glob("*.epub"))
    if not produced:
        raise AozoraEpub3Error(
            f"EPUBが出力されませんでした(dst_dir={dst_dir}):\n{stdout.strip()}"
        )

    return AozoraEpub3Result(
        epub_path=produced[-1], stdout=stdout, warnings=error_lines + warn_lines,
    )
