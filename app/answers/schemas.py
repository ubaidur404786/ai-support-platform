"""Request and response bodies for POST /answers."""

from pydantic import BaseModel, Field


class AnswerRequest(BaseModel):
    # Bounded like every input: a question is a sentence, not a document.
    question: str = Field(min_length=1, max_length=500)


class AnswerSource(BaseModel):
    """One chunk the model was shown: what [1], [2] ... in the answer refer to."""

    number: int
    document_id: int
    document_title: str
    chunk_index: int
    # Cosine similarity between the question and this chunk.
    score: float
    text: str


class AnswerResponse(BaseModel):
    question: str
    answer: str
    # False = "not found": no relevant chunk, or the model said it did not know.
    # A client should show the sources, not a guess, when this is false.
    answered: bool
    reason: str
    sources: list[AnswerSource]
