from typing import Literal

from pydantic import Field

from ..schemas import ID, SHA256, BBox, Contract, Text


class CorpusRegion(Contract):
    region_id: ID
    kind: Literal["Text", "Title", "Product Image", "illustration", "Table", "graphic"]
    text: str
    original_xywh: tuple[float, float, float, float]
    bbox: BBox


class CorpusPage(Contract):
    page_id: ID
    manual_id: ID
    index_0based: int = Field(ge=0)
    image_sha256: SHA256
    image_name: str = Field(pattern=r"^[0-9a-f]{64}\.(png|jpg|jpeg|webp)$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    regions: tuple[CorpusRegion, ...]


class Relation(Contract):
    relation_id: ID
    source_evidence_id: ID
    target_evidence_id: ID
    kind: Literal["nearby_text", "belongs_to_page"]
    basis: Text
    inferred: bool = True
