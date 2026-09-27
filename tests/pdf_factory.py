"""Build small, real PDF files in memory, for tests and measurements.

Written by hand instead of adding a PDF-writing library as a dependency: the
tests need text-bearing PDFs of a known size and page count, nothing more. The
output is a valid PDF that pypdf - and any PDF viewer - can open.
"""


def _escape(text: str) -> str:
    # Parentheses delimit strings in PDF syntax and backslash escapes; all three
    # must be escaped inside a string.
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str]) -> bytes:
    """One PDF page per string. Lines are split on "\\n".

    Text must be Latin-1: the standard Helvetica font has no other characters.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)  # PDF object numbers start at 1

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = len(objects) + 1 + 2 * len(pages)  # reserved: written last
    page_ids = []
    for text in pages:
        lines = text.split("\n")
        stream = "BT /F1 10 Tf 12 TL 40 800 Td " + " ".join(
            f"({_escape(line)}) Tj T*" for line in lines
        ) + " ET"
        data = stream.encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(data), data))
        page_ids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_id, font, content)
            )
        )
    kids = b" ".join(b"%d 0 R" % page for page in page_ids)
    add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids)))
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog,
        xref,
    )
    return bytes(out)
