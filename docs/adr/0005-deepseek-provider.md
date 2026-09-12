# ADR 0005: User-selected DeepSeek provider

Date: 2026-09-09. Accepted and capability-tested.

The user replaced the previous model selection with DeepSeek, first v4-flash and
then explicitly deepseek-v4-flash-vision-exp. The active private configuration is
LLM.config. Keep LLM.comfig as historical configuration; do not copy credentials
into source, logs or artifacts. Both files are ignored by Git.

Provider routing uses the official DeepSeek Responses endpoint. Its developer
role is mapped to user by the provider, so trusted instructions are sent as system
messages. Thinking is explicitly disabled with reasoning.effort=none; temperature=0.
JSON Schema outputs are locally validated. No tools, automatic redirects or retry
loops are added. These details follow the official
[Responses guide](https://api-docs.deepseek.com/guides/responses_api/) and
[request reference](https://api-docs.deepseek.com/api/create-response/).

Only the explicitly configured vision variant enables images; plain v4-flash is
rejected for B2 before sending requests. Never rely on the server silently replacing
images with placeholders. The user-selected vision variant passed a real image
reading probe and a three-call B2 synthetic-panel run. Full model benchmark quality
is still unmeasured.

--test-retrieval explicitly permits hash/RRF retrieval with the real configured LLM
for engineering probes. Component modes and the overall fake/engineering label
remain visible. This is separate from --fake, which uses a scripted LLM gateway,
and cannot be used to claim real BGE performance or official benchmark results.
