"""Intent examples, never manual evidence. Explicit branches and rank fusion."""

import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path
from time import monotonic
from typing import TypedDict


class Signal(TypedDict):
    hits: list[dict]
    keyword_score: int
    slot_score: int


VERSION = "intent-candidates-v1"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def normalized(text):
    # Used only for retrieval. All evidence spans refer to the unmodified input.
    return " ".join(unicodedata.normalize("NFKC", text).split())


def unit(vector, dimension):
    if len(vector) != dimension or any(not math.isfinite(x) for x in vector):
        raise ValueError("INTENT_VECTOR_INVALID")
    norm = math.sqrt(sum(x * x for x in vector))
    if not norm:
        raise ValueError("INTENT_VECTOR_INVALID")
    return [x / norm for x in vector]


class CandidateRetriever:
    """Small exact vector index with reproducible export; no trained BERT fallback."""

    def __init__(
        self, taxonomy, embedder, reranker=None, rules_path=None, classifier=None
    ):
        started = monotonic()
        if (
            embedder.identity.model_mode == "real"
            and reranker is not None
            and reranker.model_mode != "real"
        ):
            raise ValueError("INTENT_REAL_PROFILE_REQUIRES_REAL_RERANKER")
        self.classifier = classifier
        self.taxonomy = taxonomy
        self.embedder = embedder
        self.reranker = reranker
        self.rules = json.loads(
            Path(
                rules_path or Path(__file__).with_name("candidate_rules.json")
            ).read_text()
        )
        allowed = {d.intent_id for d in taxonomy.definitions}
        if (
            self.rules["taxonomy_version"] != taxonomy.version
            or not set(self.rules["rules"]) <= allowed
        ):
            raise ValueError("INTENT_RULES_VERSION_INVALID")
        self.records = []
        for definition in taxonomy.definitions:
            for example in dict.fromkeys(
                (definition.description, *definition.examples)
            ):
                self.records.append(
                    {
                        "example_id": "intent_example_"
                        + digest([definition.intent_id, example])[:24],
                        "intent_id": definition.intent_id,
                        "text": example,
                    }
                )
        self.manifest = {
            "version": VERSION,
            "namespace": "intent_examples",
            "taxonomy_sha256": digest(taxonomy.model_dump(mode="json")),
            "rules_sha256": digest(self.rules),
            "embedding": embedder.identity.model_dump(mode="json"),
            "reranker_mode": getattr(reranker, "model_mode", "disabled"),
            "reranker_model": getattr(reranker, "model_id", None),
            "reranker_revision": getattr(reranker, "revision", None),
            "examples_sha256": digest(self.records),
        }
        vectors = embedder.embed_documents([r["text"] for r in self.records])
        if len(vectors) != len(self.records):
            raise ValueError("INTENT_VECTOR_COUNT_INVALID")
        self.vectors = [unit(v, embedder.identity.dimension) for v in vectors]
        self.index_build_ms = (monotonic() - started) * 1000

    def export(self, path):
        payload = {
            "manifest": self.manifest,
            "records": self.records,
            "vectors": self.vectors,
        }
        Path(path).write_text(
            json.dumps(
                {"sha256": digest(payload), **payload}, ensure_ascii=False, indent=2
            )
            + "\n"
        )

    def retrieve(self, raw_query, state):
        started = monotonic()
        query = normalized(raw_query)
        slots = []
        for name, pattern in {
            "ticket": r"\bticket_[A-Za-z0-9_-]+\b",
            "order_item": r"\bitem_[A-Za-z0-9_-]+\b",
            "model": r"\b[A-Z]{2,}[A-Z0-9]*-[A-Z0-9-]+\b",
        }.items():
            for match in re.finditer(pattern, raw_query):
                slots.append(
                    {
                        "name": name,
                        "value": match.group(),
                        "start": match.start(),
                        "end": match.end(),
                        "basis": "format_only_unverified",
                    }
                )
        signal: dict[str, Signal] = {}
        for intent, rules in self.rules["rules"].items():
            hits = []
            for kind in ["positive", "negative"]:
                for literal in rules[kind]:
                    for match in re.finditer(re.escape(literal), raw_query):
                        hits.append(
                            {
                                "kind": kind,
                                "quote": match.group(),
                                "start": match.start(),
                                "end": match.end(),
                            }
                        )
            signal[intent] = {
                "hits": hits,
                "keyword_score": sum(
                    1 if h["kind"] == "positive" else -1 for h in hits
                ),
                "slot_score": sum(s["name"] in rules["slots"] for s in slots),
            }
        qv = unit(self.embedder.embed_query(query), self.embedder.identity.dimension)
        scored = sorted(
            [
                (sum(a * b for a, b in zip(qv, v, strict=True)), r)
                for v, r in zip(self.vectors, self.records, strict=True)
            ],
            key=lambda pair: (-pair[0], pair[1]["example_id"]),
        )[:10]
        grouped: dict[str, list[float]] = {}
        for score, record in scored:
            grouped.setdefault(record["intent_id"], []).append(score)
        emb = {
            k: 0.6 * max(v) + 0.4 * sum(v[:3]) / len(v[:3]) for k, v in grouped.items()
        }
        channels = {
            "embedding": sorted(emb, key=lambda i: (-emb[i], i))[:3],
            "keyword": sorted(
                [i for i in signal if signal[i]["hits"]],
                key=lambda i: (-signal[i]["keyword_score"], i),
            ),
            "slot": sorted(
                [i for i in signal if signal[i]["slot_score"]],
                key=lambda i: (-signal[i]["slot_score"], i),
            ),
        }
        bert = {"enabled": False, "reason": "no_trained_classifier_selected"}
        if self.classifier is not None:
            values = self.classifier.scores(query)
            if set(values) != {d.intent_id for d in self.taxonomy.definitions} or any(
                not math.isfinite(v) or not 0 <= v <= 1 for v in values.values()
            ):
                raise ValueError("INTENT_CLASSIFIER_SCORES_INVALID")
            channels["bert"] = sorted(values, key=lambda i: (-values[i], i))[:3]
            bert = {
                "enabled": True,
                "identity": self.classifier.identity,
                "scores": values,
            }
        fusion: dict[str, float] = {}
        for ids in channels.values():
            for rank, intent in enumerate(ids, 1):
                fusion[intent] = fusion.get(intent, 0) + 1 / (60 + rank)
        fused = sorted(fusion, key=lambda i: (-fusion[i], i))[:5]
        reranked = fused
        rescored = []
        if self.reranker is not None and fused:
            definitions = {d.intent_id: d for d in self.taxonomy.definitions}
            pairs = [
                json.dumps(definitions[i].model_dump(mode="json"), ensure_ascii=False)
                for i in fused
            ]
            rescored = self.reranker.scores(query, pairs, [fusion[i] for i in fused])
            if len(rescored) != len(fused) or any(
                not math.isfinite(x) for x in rescored
            ):
                raise ValueError("INTENT_RERANK_INVALID")
            reranked = sorted(fused, key=lambda i: (-rescored[fused.index(i)], i))
        protected = sorted(
            {
                g["intent_id"]
                for g in state.get("goals", {}).values()
                if g["status"] in {"active", "suspended", "pending"}
            }
        )
        # Six-label catalogue: keep all labels for the Judge, so candidate pruning cannot
        # silently drop a second goal. Large-catalogue pruning requires separate validation.
        judge_ids = [d.intent_id for d in self.taxonomy.definitions]
        if len(judge_ids) > 32:
            raise ValueError("INTENT_CATALOG_BUDGET_REQUIRES_CONFIGURATION")
        ranked_scores = sorted(emb.values(), reverse=True)
        return {
            "version": VERSION,
            "manifest": self.manifest,
            "raw_query": raw_query,
            "normalized_query": query,
            "slots": slots,
            "signals": signal,
            "example_hits": [{"score": score, **r} for score, r in scored],
            "embedding_scores": emb,
            "embedding_margin": ranked_scores[0] - ranked_scores[1]
            if len(ranked_scores) > 1
            else None,
            "channels": channels,
            "fused_top5": fused,
            "reranked_top3": reranked[:3],
            "reranker_scores": dict(zip(fused, rescored, strict=True))
            if rescored
            else {},
            "protected_intents": protected,
            "judge_intent_ids": judge_ids,
            "catalog_policy": "full_catalog_recall_guard",
            "bert": bert,
            "index_build_ms": self.index_build_ms,
            "latency_ms": (monotonic() - started) * 1000,
        }
