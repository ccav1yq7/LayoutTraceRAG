from typing import Literal

from pydantic import Field, model_validator

from .schemas import SHA256, Contract, Text


class ModelSpec(Contract):
    model_id: Text
    revision: SHA256 | None = (
        None  # Artifact SHA256; use source_revision for HF commit.
    )
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    endpoint_env: str = Field(pattern=r"^SG_[A-Z0-9_]+_BASE_URL$")
    api_key_env: str = Field(pattern=r"^SG_[A-Z0-9_]+_API_KEY$")


class DataManifest(Contract):
    dataset_id: Literal["pm209", "ecom"]
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    archive_sha256: SHA256
    audit_status: Literal["passed"]
    license_review_status: Literal["approved_for_research"]


class Settings(Contract):
    model_mode: Literal["fake", "real"] = "fake"
    formal: bool = False
    planner: ModelSpec | None = None
    vision: ModelSpec | None = None
    data_manifest: DataManifest | None = None
    allow_generated_images: Literal[False] = False
    allow_real_business_writes: Literal[False] = False

    @model_validator(mode="after")
    def formal_gate(self):
        if self.formal:
            if self.model_mode != "real" or self.data_manifest is None:
                raise ValueError(
                    "formal evaluation requires real models and audited manifest"
                )
            for model in (self.planner, self.vision):
                if model is None or not (model.revision or model.source_revision):
                    raise ValueError(
                        "formal evaluation requires pinned planner/vision revisions"
                    )
        return self
