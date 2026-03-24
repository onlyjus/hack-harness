"""PDF text extraction using PyMuPDF (fitz).

Provides page-by-page and full-document text extraction for downloaded PDFs.
"""

from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


@dataclass
class PageText:
    """Text content from a single PDF page."""

    page_number: int  # 1-based
    text: str


def extract_text(pdf_path: str | Path) -> str:
    """Extract all text from a PDF file.

    Args:
        pdf_path: Path to a PDF file.

    Returns:
        Concatenated text from all pages, separated by page-break markers.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        RuntimeError: If the PDF cannot be opened or parsed.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise RuntimeError(f"Failed to open PDF {path.name}: {exc}") from exc

    pages: list[str] = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text").strip()
        if text:
            pages.append(f"--- Page {page_num + 1} ---\n{text}")

    doc.close()
    return "\n\n".join(pages)


def extract_text_by_page(pdf_path: str | Path) -> list[PageText]:
    """Extract text from each page of a PDF.

    Args:
        pdf_path: Path to a PDF file.

    Returns:
        List of PageText objects, one per page that contains text.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise RuntimeError(f"Failed to open PDF {path.name}: {exc}") from exc

    results: list[PageText] = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text").strip()
        if text:
            results.append(PageText(page_number=page_num + 1, text=text))

    doc.close()
    return results


def extract_first_n_pages(pdf_path: str | Path, n: int = 3) -> str:
    """Extract text from the first N pages of a PDF.

    Useful for directive metadata extraction where the title, identifier,
    and date typically appear on the first few pages.

    Args:
        pdf_path: Path to a PDF file.
        n: Maximum number of pages to extract (default 3).

    Returns:
        Concatenated text from up to the first N pages.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise RuntimeError(f"Failed to open PDF {path.name}: {exc}") from exc

    pages: list[str] = []
    limit = min(n, len(doc))
    for page_num in range(limit):
        page = doc[page_num]
        text = page.get_text("text").strip()
        if text:
            pages.append(f"--- Page {page_num + 1} ---\n{text}")

    doc.close()
    return "\n\n".join(pages)


def get_page_count(pdf_path: str | Path) -> int:
    """Return the number of pages in a PDF."""
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    doc = fitz.open(str(path))
    count = len(doc)
    doc.close()
    return count
