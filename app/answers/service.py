"""Answer a question from the knowledge base: retrieve, check, generate.

This is retrieval-augmented generation (RAG). The language model is not asked
what IT knows - a 1.5B model knows little, and nothing about this company. It is
given the few chunks that semantic search found and told to answer from them
only. The answer comes back with those chunks as its sources, so a person can
check it.

Two guards stand between a bad question and a made-up answer:

1. The relevance threshold. If no chunk scores above it, the model is not
   called at all: "not found", in milliseconds.
2. The instructions. If the chunks are related but do not contain the answer,
   the model is told to reply "I don't know" - and that reply also becomes
   "not found".
"""

from collections.abc import Iterator
from dataclasses import dataclass

from app.answers.generator import generate, generate_stream
from app.documents.repository import SearchHit
from app.documents.service import DocumentService

NOT_FOUND = "I could not find an answer to that in the knowledge base."

# How the model says the context does not answer the question. It is told to
# reply "I don't know", but measured in v10 it sometimes says the same thing in
# its own words ("the context does not mention merging tickets"). Checked
# case-insensitively, anywhere in the reply.
MODEL_DECLINES = ("i don't know", "does not mention", "does not provide", "does not contain")

# Measured in v10 (scripts/evaluate_rag.py): a first version also asked the
# model to cite "[1]". The 0.5B model then often replied with nothing but the
# citation ("[1] Refund policy") - correct answers 0.17. Citations do not need
# the model: the API returns the chunks it was given as numbered sources.
SYSTEM_PROMPT = (
    "You answer customer support questions. Use ONLY the information in the "
    "context. Answer in one or two complete sentences. If the context does not "
    "contain the answer, reply exactly: I don't know."
)


@dataclass
class Answer:
    text: str
    answered: bool
    # Why: "answered", "no_relevant_sources" (guard 1) or "model_declined" (guard 2).
    reason: str
    # The chunks the model was shown - the citations. Empty when it was not called.
    sources: list[SearchHit]


def build_messages(question: str, sources: list[SearchHit]) -> list[dict]:
    """The chat sent to the model: instructions, then the context and the question."""
    context = "\n\n".join(f"{hit.document_title}\n{hit.text}" for hit in sources)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n\n{context}\n\nQuestion: {question}"},
    ]


def model_declined(reply: str) -> bool:
    """Guard 2: did the model say the context does not answer the question?"""
    return any(phrase in reply.lower() for phrase in MODEL_DECLINES)


# How much of a streamed reply is held back before the first piece is sent
# (v11). A refusal ("I don't know.") comes at the start of the reply, and it
# must never reach the user as if it were an answer. Long enough to contain
# every phrase in MODEL_DECLINES, short enough to cost only a few tokens.
DECLINE_CHECK_CHARS = 40


class AnswerService:
    def __init__(
        self,
        documents: DocumentService,
        relevance_threshold: float,
        max_sources: int,
        max_tokens: int,
    ) -> None:
        self._documents = documents
        self._relevance_threshold = relevance_threshold
        self._max_sources = max_sources
        self._max_tokens = max_tokens

    def retrieve(self, organization_id: int, question: str) -> list[SearchHit]:
        """Steps 1 and 2: the chunks relevant enough to answer from. [] = not found.

        Step 1 - retrieve: the same semantic search as GET /documents/search, so
        the same tenant filter and model version rules apply. Raises
        EmptySearchQuery (422) or EmbeddingUnavailable (503).
        Step 2 - check: keep only chunks above the threshold (guard 1).
        """
        hits = self._documents.search(
            organization_id, question, limit=self._max_sources, mode="semantic"
        )
        return [hit for hit in hits if hit.rank >= self._relevance_threshold]

    def answer(self, organization_id: int, question: str) -> Answer:
        """The whole answer at once (POST /answers)."""
        sources = self.retrieve(organization_id, question)
        if not sources:
            return Answer(NOT_FOUND, answered=False, reason="no_relevant_sources", sources=[])

        # Step 3 - generate, from those chunks only. Raises GenerationUnavailable.
        reply = generate(build_messages(question, sources), self._max_tokens)
        if model_declined(reply):
            return Answer(NOT_FOUND, answered=False, reason="model_declined", sources=sources)
        return Answer(reply, answered=True, reason="answered", sources=sources)

    def stream(self, question: str, sources: list[SearchHit]) -> Iterator[dict]:
        """The answer in pieces, as events (POST /answers/stream, v11).

        Call retrieve() first: its errors must happen before streaming starts.
        Yields, in order:
            {"type": "text", "text": "..."}      zero or more times
            {"type": "done", "answered": ..., "reason": ..., "answer": "..."}
        """
        if not sources:
            yield done_event(NOT_FOUND, answered=False, reason="no_relevant_sources")
            return

        reply = ""
        sent = False  # has any text reached the client yet?
        for piece in generate_stream(build_messages(question, sources), self._max_tokens):
            reply += piece
            if not sent:
                # Hold the start back until it can be checked for a refusal.
                if len(reply) < DECLINE_CHECK_CHARS:
                    continue
                if model_declined(reply):
                    # Stop here: the model is not asked for the rest of an
                    # answer that will not be shown - that saves CPU as well.
                    yield done_event(NOT_FOUND, answered=False, reason="model_declined")
                    return
                sent = True
                yield {"type": "text", "text": reply}
            else:
                yield {"type": "text", "text": piece}

        reply = reply.strip()
        # A refusal can also come late ("Based on the context, it does not
        # mention...") or in a reply shorter than the check. The final event
        # has the last word: a client must show "not found" if it says so.
        if model_declined(reply):
            yield done_event(NOT_FOUND, answered=False, reason="model_declined")
        else:
            if not sent and reply:
                yield {"type": "text", "text": reply}
            yield done_event(reply, answered=True, reason="answered")


def done_event(answer: str, answered: bool, reason: str) -> dict:
    return {"type": "done", "answered": answered, "reason": reason, "answer": answer}
