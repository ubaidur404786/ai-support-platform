"""Split a document's text into overlapping, search-sized chunks.

Why split at all: a search should return the paragraph that answers a question,
not a 30-page file. Later versions embed and retrieve chunks, and generated
answers quote them - so the chunk is the unit of retrieval from here on.

Why overlap: a boundary can fall in the middle of the sentence that matters.
Repeating the last ~100 characters of each chunk at the start of the next means
that sentence appears whole in at least one of them.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    # text == document[start:end], so a result can be located in the original.
    start: int
    end: int


def _best_break(text: str, start: int, limit: int) -> int:
    """Where to end a chunk that starts at `start` and may not pass `limit`.

    Prefers the most natural break available in the second half of the window:
    a paragraph, then a sentence, then a word. Only the second half, so a break
    found right at the start cannot produce a tiny chunk.
    """
    window_floor = start + (limit - start) // 2
    for separator in ("\n\n", ". ", "\n", " "):
        position = text.rfind(separator, window_floor, limit)
        if position != -1:
            # Keep the separator's first character (the full stop, the newline)
            # with the chunk it ends.
            return position + 1
    # One unbroken run of characters longer than half a chunk: cut it.
    return limit


def chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> list[Chunk]:
    if max_chars <= 0 or not 0 <= overlap < max_chars // 2:
        # An overlap of half the chunk or more would make each chunk mostly a
        # copy of the previous one, and could stop the loop from advancing.
        raise ValueError("need max_chars > 0 and 0 <= overlap < max_chars / 2")

    chunks: list[Chunk] = []
    start = 0
    length = len(text)

    while start < length:
        limit = min(start + max_chars, length)
        end = length if limit == length else _best_break(text, start, limit)

        # Trim whitespace at the edges but keep the offsets exact.
        chunk_start, chunk_end = start, end
        while chunk_start < chunk_end and text[chunk_start].isspace():
            chunk_start += 1
        while chunk_end > chunk_start and text[chunk_end - 1].isspace():
            chunk_end -= 1
        if chunk_end > chunk_start:
            chunks.append(
                Chunk(
                    index=len(chunks),
                    text=text[chunk_start:chunk_end],
                    start=chunk_start,
                    end=chunk_end,
                )
            )

        if end >= length:
            break

        # Step back by the overlap, then forward to the start of a word, so the
        # next chunk does not begin with half a word.
        next_start = max(end - overlap, start + 1)
        while next_start < end and not text[next_start - 1].isspace():
            next_start += 1
        start = next_start

    return chunks
