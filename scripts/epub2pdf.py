#!/usr/bin/env python3

"""任意のEPUBファイルをPDFに変換するCLIツール。

変換エンジン本体は `narou_dl.pdf_builder` にある(narou-dlのダウンロード
パイプラインが生成したEPUBに限らず、任意のEPUBファイルに対して使える
汎用ツールとして、このスクリプトはそちらへの薄いCLIラッパーになっている)。

要 `pip install -e ".[pdf]"` に加えて `python -m playwright install chromium`。

Usage:
    python scripts/epub2pdf.py book.epub -o book.pdf

Optional:
    python scripts/epub2pdf.py book.epub \
        -o book.pdf \
        --font-size 9pt \
        --line-height 1.8 \
        --margin 15 \
        --page-number outer \
        --no-cover \
        --no-toc \
        --writing-mode vertical-rl \
        --page-size A5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from narou_dl.pdf_builder import PdfEngineError, build_pdf  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert an EPUB to PDF via Chromium",
    )

    parser.add_argument("epub", type=Path, help="Input EPUB")
    parser.add_argument("-o", "--output", type=Path, help="Output PDF")

    parser.add_argument(
        "--font-size", default="9pt", help="Base font size (default: 9pt)",
    )
    parser.add_argument(
        "--line-height", default="1.8", help="Line height (default: 1.8)",
    )
    parser.add_argument(
        "--margin", type=float, default=15,
        help="Fallback margin in mm, used only if the EPUB's CSS "
             "doesn't declare @page margin (default: 15)",
    )
    parser.add_argument(
        "--page-number", choices=["bottom", "outer"], default="bottom",
        help="Page-number position: bottom or outer",
    )
    parser.add_argument(
        "--writing-mode", choices=["auto", "horizontal-tb", "vertical-rl"],
        default="auto",
        help="Override the writing mode instead of auto-detecting it "
             "from the EPUB's own CSS (default: auto)",
    )
    parser.add_argument(
        "--page-size", default=None,
        help='Override the page size instead of auto-detecting it from '
             '@page in the EPUB\'s CSS, e.g. "A5" or "128mm 182mm" '
             "(default: auto, falls back to A5)",
    )
    parser.add_argument(
        "--no-cover", action="store_true",
        help="Don't render the EPUB's cover image as page 1",
    )
    parser.add_argument(
        "--no-toc", action="store_true",
        help="Don't generate a table-of-contents page",
    )

    args = parser.parse_args()

    epub_path = args.epub.expanduser().resolve()

    if not epub_path.exists():
        print(f"EPUBがありません: {epub_path}", file=sys.stderr)
        return 1

    output_pdf = (
        args.output.expanduser().resolve()
        if args.output
        else epub_path.with_suffix(".pdf")
    )

    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    writing_mode = None if args.writing_mode == "auto" else args.writing_mode

    print(f"EPUB: {epub_path}")

    try:
        build_pdf(
            epub_path,
            output_pdf,
            font_size=args.font_size,
            line_height=args.line_height,
            margin=args.margin,
            page_number_position=args.page_number,
            writing_mode=writing_mode,
            page_size=args.page_size,
            include_cover=not args.no_cover,
            include_toc=not args.no_toc,
        )
    except PdfEngineError as exc:
        print(f"[エラー] PDF生成に失敗しました: {exc}", file=sys.stderr)
        return 1

    print(f"PDF: {output_pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
