"""Unit tests for extraction and chunking - no HTTP, no database.

These are the parts most likely to go wrong on real files, so they are tested
directly and exhaustively, where a failure points at one function.
"""

import pytest

from app.documents.chunking import chunk_text
from app.documents.extraction import (
    DocumentTooLarge,
    UnreadableDocument,
    UnsupportedDocumentType,
    derive_title,
    extract_text,
    normalize_text,
)
from tests.pdf_factory import make_pdf

ARTICLE = (
    "Refunds are issued to the original payment method. "
    "A refund takes five to ten business days to appear on your statement.\n\n"
    "Annual plans can be refunded within 30 days of purchase. Monthly plans are "
    "not refunded, but you can cancel at any time and keep access until the end "
    "of the billing period.\n\n"
) * 20


# --- Chunking ------------------------------------------------------------------


def test_no_chunk_exceeds_the_maximum():
    chunks = chunk_text(ARTICLE, max_chars=300, overlap=50)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 300 for chunk in chunks)


def test_offsets_point_at_the_exact_text():
    """start/end must locate the chunk in the document, for citations later."""
    for chunk in chunk_text(ARTICLE, max_chars=300, overlap=50):
        assert ARTICLE[chunk.start : chunk.end] == chunk.text


def test_no_text_is_lost():
    """Every non-whitespace character of the document is in at least one chunk.

    The failure this guards against is silent: a chunker that drops the tail of
    a document produces results that look fine and can never find that tail.
    """
    covered = [False] * len(ARTICLE)
    for chunk in chunk_text(ARTICLE, max_chars=300, overlap=50):
        for position in range(chunk.start, chunk.end):
            covered[position] = True

    missing = [i for i, char in enumerate(ARTICLE) if not char.isspace() and not covered[i]]
    assert missing == []


def test_neighbouring_chunks_overlap():
    chunks = chunk_text(ARTICLE, max_chars=300, overlap=50)

    for previous, following in zip(chunks, chunks[1:]):
        assert following.start < previous.end


def test_chunks_prefer_to_end_at_a_sentence_or_paragraph():
    chunks = chunk_text(ARTICLE, max_chars=300, overlap=50)

    # All but the last end on a full stop: no chunk stops mid-sentence when a
    # sentence boundary was available.
    assert all(chunk.text.endswith(".") for chunk in chunks[:-1])


def test_indexes_are_sequential_from_zero():
    chunks = chunk_text(ARTICLE, max_chars=300, overlap=50)

    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_short_text_is_one_chunk():
    chunks = chunk_text("Reset your password from the login page.", max_chars=800)

    assert len(chunks) == 1
    assert chunks[0].text == "Reset your password from the login page."


def test_empty_text_has_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_a_long_unbroken_word_is_cut_rather_than_looping_forever():
    chunks = chunk_text("x" * 2000, max_chars=300, overlap=50)

    assert all(len(c.text) <= 300 for c in chunks)
    assert sum(len(c.text) for c in chunks) >= 2000


@pytest.mark.parametrize("max_chars, overlap", [(0, 0), (100, 50), (100, -1), (100, 80)])
def test_settings_that_could_not_make_progress_are_rejected(max_chars, overlap):
    with pytest.raises(ValueError):
        chunk_text("some text", max_chars=max_chars, overlap=overlap)


# --- Extraction ----------------------------------------------------------------


def test_markdown_is_read_as_utf8():
    extracted = extract_text("guide.md", "# Café hours\n\nOpen 9–5.".encode(), 300)

    assert extracted.content_type == "markdown"
    assert extracted.text == "# Café hours\n\nOpen 9–5."
    assert extracted.page_count is None


def test_a_byte_order_mark_is_removed():
    extracted = extract_text("notes.txt", "﻿Hello".encode("utf-8"), 300)

    assert extracted.text == "Hello"


def test_a_pdf_is_read_page_by_page():
    data = make_pdf(["Refund policy", "Refunds take ten days."])

    extracted = extract_text("policy.pdf", data, 300)

    assert extracted.content_type == "pdf"
    assert extracted.page_count == 2
    assert "Refund policy" in extracted.text
    assert "Refunds take ten days." in extracted.text


@pytest.mark.parametrize("filename", ["virus.exe", "sheet.xlsx", "noextension"])
def test_unsupported_extensions_are_refused(filename):
    with pytest.raises(UnsupportedDocumentType):
        extract_text(filename, b"anything", 300)


def test_a_file_named_pdf_must_actually_be_a_pdf():
    """The extension is chosen by the client; the first bytes are not."""
    with pytest.raises(UnsupportedDocumentType):
        extract_text("policy.pdf", b"just some text", 300)


def test_a_corrupt_pdf_is_unreadable_not_a_crash():
    with pytest.raises(UnreadableDocument):
        extract_text("broken.pdf", b"%PDF-1.4\nthis is not really a pdf", 300)


def test_a_pdf_without_text_is_refused():
    """What a scanned document looks like to us: pages, but no text layer."""
    with pytest.raises(UnreadableDocument):
        extract_text("scan.pdf", make_pdf(["", ""]), 300)


def test_a_pdf_over_the_page_limit_is_refused_before_extraction():
    with pytest.raises(DocumentTooLarge):
        extract_text("manual.pdf", make_pdf(["page"] * 5), max_pages=4)


def test_non_utf8_text_is_refused():
    with pytest.raises(UnreadableDocument):
        extract_text("latin1.txt", "café".encode("latin-1"), 300)


def test_whitespace_only_text_is_refused():
    with pytest.raises(UnreadableDocument):
        extract_text("blank.txt", b"  \n\n\t ", 300)


def test_normalize_removes_nul_and_unifies_line_endings():
    """PostgreSQL cannot store NUL in a text column; the insert would fail."""
    assert normalize_text("a\x00b\r\nc\rd\n\n\n\ne  \nf") == "ab\nc\nd\n\ne\nf"


@pytest.mark.parametrize(
    "filename, text, expected",
    [
        ("refunds.md", "# Refund policy\n\nBody", "Refund policy"),
        ("refunds.md", "## \n\nBody", "refunds"),
        ("Refund Policy.pdf", "Plain first line", "Refund Policy"),
    ],
)
def test_title_comes_from_the_heading_or_the_filename(filename, text, expected):
    assert derive_title(filename, text) == expected
