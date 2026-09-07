# Third-party components and data

The MIT license in this repository applies to its own code. Dependencies, downloaded model weights,
datasets, subtitles and video assets are separate works. No third-party weights or videos are included.

| Component | Source | Use |
| --- | --- | --- |
| LangGraph / LangChain | https://github.com/langchain-ai/langgraph · https://github.com/langchain-ai/langchain | Orchestration / provider adapters |
| LanceDB | https://github.com/lancedb/lancedb | Embedded vector and full-text index |
| BGE-M3 | https://huggingface.co/BAAI/bge-m3 | Downloaded semantic embeddings |
| BGE reranker | https://huggingface.co/BAAI/bge-reranker-v2-m3 | Downloaded cross-encoder |
| CLIP / SigLIP | https://github.com/openai/CLIP · https://huggingface.co/google | Optional visual-model experiments |
| faster-whisper | https://github.com/SYSTRAN/faster-whisper | Speech transcription; model weights downloaded separately |
| PyAV / RapidOCR | https://github.com/PyAV-Org/PyAV · https://github.com/RapidAI/RapidOCR | Video decoding / frame OCR |
| TVQA-Long | https://huggingface.co/datasets/Vision-CAIR/TVQA-Long | Historical benchmark data, not bundled |
| How2QA | https://github.com/antoyang/just-ask | Optional benchmark acquisition, not bundled |

Package versions and artifact hashes are recorded in `uv.lock`. For downloaded assets, check the license
and source terms at the exact revision you obtain; package licensing does not grant rights to the associated
videos. Download scripts operate only on user-supplied manifests and do not grant redistribution rights.

Historical reports do not record every model/data revision. These missing revisions remain unknown;
the reports are retained as historical measurements rather than a fully pinned reproducibility claim.
New runs should record dataset and model revisions, configuration, hardware and source commit.

If a provider is configured, questions and retrieved evidence are transmitted to that provider.
See [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data) or the chosen
provider's current data-processing terms. The local heuristic generator makes no provider calls;
semantic/ASR/OCR models may still download assets on their first use.

