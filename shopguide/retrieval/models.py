import hashlib
import math
import re
from typing import Literal, Protocol

from pydantic import Field, model_validator

from ..schemas import Contract, Text


class IndexIdentity(Contract):
    model_id: Text
    revision: Text
    dimension: int = Field(gt=0)
    preprocessing: Text
    schema_version: Literal[2] = 2
    model_mode: Literal["fake", "real"]

    @model_validator(mode="after")
    def pinned(self):
        if self.model_mode == "real" and not re.fullmatch(
            r"[0-9a-f]{40}", self.revision
        ):
            raise ValueError("real embeddings require a pinned source revision")
        return self


class Embedder(Protocol):
    identity: IndexIdentity

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, query: str) -> list[float]: ...


class Reranker(Protocol):
    model_mode: str

    def scores(
        self, query: str, texts: list[str], rrf_scores: list[float]
    ) -> list[float]: ...


class HashEmbedder:
    """Explicit engineering fake, never a semantic model or benchmark baseline."""

    identity = IndexIdentity(
        model_id="test/hash",
        revision="fake-v1",
        dimension=32,
        preprocessing="casefold-word-hash-v1",
        model_mode="fake",
    )

    def embed_query(self, query):
        vector = [0.0] * self.identity.dimension
        for token in re.findall(r"\w+", query.casefold()):
            digest = hashlib.sha256(token.encode()).digest()
            vector[digest[0] % len(vector)] += 1.0
        if not any(vector):
            vector[0] = 1.0
        norm = math.sqrt(sum(x * x for x in vector))
        return [x / norm for x in vector]

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


class RRFReranker:
    """No semantic reranking; explicit offline engineering profile."""

    model_mode = "fake"

    def scores(self, query, texts, rrf_scores):
        return rrf_scores


class BGEEmbedder:
    def __init__(
        self,
        model_id: str,
        revision: str,
        *,
        local_files_only: bool = True,
        device: str | None = None,
        batch_size: int = 4,
        max_length: int = 1024,
    ):
        if batch_size < 1 or not 1 <= max_length <= 8192:
            raise ValueError("invalid BGE batch/length budget")
        self.batch_size = batch_size
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("pinned revision required")
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(
            model_id,
            revision=revision,
            local_files_only=local_files_only,
            device=device,
        )
        self.model.max_seq_length = max_length
        self.identity = IndexIdentity(
            model_id=model_id,
            revision=revision,
            dimension=self.model.get_sentence_embedding_dimension(),
            preprocessing=f"normalize-no-prefix-max{max_length}-v2",
            model_mode="real",
        )

    def embed_documents(self, texts):
        return self.model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=self.batch_size,
            show_progress_bar=False,
        ).tolist()

    def embed_query(self, query):
        return self.embed_documents([query])[0]


class BGEReranker:
    model_mode = "real"

    def __init__(
        self,
        model_id: str,
        revision: str,
        *,
        local_files_only: bool = True,
        device: str | None = None,
        batch_size: int = 4,
        max_length: int = 1024,
    ):
        if batch_size < 1 or not 1 <= max_length <= 8192:
            raise ValueError("invalid BGE batch/length budget")
        self.batch_size = batch_size
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("pinned revision required")
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(
            model_id,
            revision=revision,
            local_files_only=local_files_only,
            max_length=max_length,
            device=device,
        )
        self.model_id = model_id
        self.revision = revision

    def scores(self, query, texts, rrf_scores):
        return self.model.predict(
            [(query, t) for t in texts],
            batch_size=self.batch_size,
            show_progress_bar=False,
        ).tolist()
