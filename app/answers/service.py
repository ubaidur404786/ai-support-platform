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

from dataclasses import dataclass

from app.answers.generator import generate
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

    def answer(self, organization_id: int, question: str) -> Answer:
        # Step 1 - retrieve: the same semantic search as GET /documents/search,
        # so the same tenant filter and the same model version rules apply.
        # Raises EmptySearchQuery (422) or EmbeddingUnavailable (503).
        hits = self._documents.search(
            organization_id, question, limit=self._max_sources, mode="semantic"
        )

        # Step 2 - check: keep only chunks that are relevant enough (guard 1).
        sources = [hit for hit in hits if hit.rank >= self._relevance_threshold]
        if not sources:
            return Answer(NOT_FOUND, answered=False, reason="no_relevant_sources", sources=[])

        # Step 3 - generate, from those chunks only. Raises GenerationUnavailable.
        reply = generate(build_messages(question, sources), self._max_tokens)
        if any(phrase in reply.lower() for phrase in MODEL_DECLINES):
            # Guard 2: related chunks, but not the answer.
            return Answer(NOT_FOUND, answered=False, reason="model_declined", sources=sources)
        return Answer(reply, answered=True, reason="answered", sources=sources)
