"""Turn uploaded bytes into plain text.

Plain functions with no HTTP and no database, so the parts most likely to break
on real-world files can be tested directly, one odd file at a time.
"""

import io
import re
from dataclasses import dataclass
from pathlib import PurePath

# pypdf reads PDF files in pure Python - no system libraries to install, which
# keeps the project runnable on any machine and in a slim Docker image.
from pypdf import PdfReader
from pypdf.errors import PdfReadError

SUPPORTED_EXTENSIONS = {".txt": "text", ".md": "markdown", ".pdf": "pdf"}


class UnsupportedDocumentType(ValueError):
    """The file is not a type we can read. Becomes 415."""


class UnreadableDocument(ValueError):
    """The file claims a supported type but its content cannot be used. Becomes 422."""


class DocumentTooLarge(ValueError):
    """The file is within the byte limit but would still cost too much work. Becomes 413."""


@dataclass(frozen=True)
class ExtractedDocument:
    content_type: str
    text: str
    page_count: int | None


def detect_type(filename: str, data: bytes) -> str:
    """Decide the file type from its name AND its first bytes.

    The client's Content-Type header is ignored: the client chooses it, so it
    proves nothing. The extension alone is not trusted either - a PDF must also
    start with the PDF signature, "%PDF-".
    """
    extension = PurePath(filename).suffix.lower()
    content_type = SUPPORTED_EXTENSIONS.get(extension)
    if content_type is None:
        raise UnsupportedDocumentType(
            f"Unsupported file type {extension or '(none)'}; use .txt, .md or .pdf"
        )
    if content_type == "pdf" and not data.startswith(b"%PDF-"):
        raise UnsupportedDocumentType("File has a .pdf name but is not a PDF")
    return content_type


def normalize_text(text: str) -> str:
    # PostgreSQL text columns cannot store the NUL character at all - an insert
    # containing one fails. PDF extraction produces them surprisingly often.
    text = text.replace("\x00", "")
    # One line-ending convention, so chunk boundaries and offsets are consistent
    # whichever operating system wrote the file.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Trailing spaces carry no meaning and make "is this line blank?" unreliable.
    text = re.sub(r"[ \t]+\n", "\n", text)
    # Three or more newlines are still one paragraph break.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_pdf(data: bytes, max_pages: int) -> tuple[str, int]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UnreadableDocument("Encrypted PDFs are not supported")
        page_count = len(reader.pages)
        # Checked before extracting anything: the page count is known from the
        # file's structure, and extraction is where the time goes.
        if page_count > max_pages:
            raise DocumentTooLarge(
                f"PDF has {page_count} pages; the limit is {max_pages}"
            )
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as error:
        raise UnreadableDocument(f"Could not read PDF: {error}") from error
    # A blank line between pages, so the last paragraph of one page and the
    # first of the next are never glued into one word.
    return "\n\n".join(pages), page_count


def extract_text(filename: str, data: bytes, max_pages: int) -> ExtractedDocument:
    content_type = detect_type(filename, data)

    page_count = None
    if content_type == "pdf":
        raw, page_count = _extract_pdf(data, max_pages)
    else:
        try:
            # utf-8-sig also removes the invisible byte-order mark that some
            # Windows editors put at the start of a file.
            raw = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise UnreadableDocument("Text files must be UTF-8 encoded") from error

    text = normalize_text(raw)
    if not text:
        # A scanned PDF is a picture of text: it has pages but no text layer.
        # Storing it would create a document that no search can ever find.
        raise UnreadableDocument(
            "No extractable text (a scanned PDF needs OCR, which is not supported)"
        )
    return ExtractedDocument(content_type=content_type, text=text, page_count=page_count)


def derive_title(filename: str, text: str) -> str:
    """A Markdown file's first heading, otherwise the file name without extension."""
    first_line = text.split("\n", 1)[0].strip()
    if first_line.startswith("#"):
        heading = first_line.lstrip("#").strip()
        if heading:
            return heading[:300]
    return (PurePath(filename).stem or "Untitled")[:300]
