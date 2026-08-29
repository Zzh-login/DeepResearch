"""Isolated Microsoft Word COM process used for DOCX-to-PDF conversion."""

import argparse
from pathlib import Path


PDF_FORMAT = 17


def convert(docx_path: Path, pdf_path: Path) -> None:
    import pythoncom
    import win32com.client

    source = docx_path.resolve(strict=True)
    target = pdf_path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(
            str(source),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            Visible=False,
        )
        document.ExportAsFixedFormat(
            OutputFileName=str(target),
            ExportFormat=PDF_FORMAT,
            OpenAfterExport=False,
            OptimizeFor=0,
            Range=0,
            Item=0,
            IncludeDocProps=True,
            KeepIRM=True,
            CreateBookmarks=1,
            DocStructureTags=True,
            BitmapMissingFonts=True,
            UseISO19005_1=False,
        )
    finally:
        try:
            if document is not None:
                document.Close(SaveChanges=False)
        finally:
            try:
                if word is not None:
                    word.Quit(SaveChanges=False)
            finally:
                pythoncom.CoUninitialize()

    if not target.is_file() or target.stat().st_size <= 0:
        raise RuntimeError("Microsoft Word 未生成 PDF 文件")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("docx_path", type=Path)
    parser.add_argument("pdf_path", type=Path)
    args = parser.parse_args()
    convert(args.docx_path, args.pdf_path)


if __name__ == "__main__":
    main()
