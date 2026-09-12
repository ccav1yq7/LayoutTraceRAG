# Fixed RAG prompts

The canonical, wheel-packaged instructions are in
`shopguide/qa/prompts.py` (`fixed-rag-v3`). Selection, writing and
verification have separate instructions. Runtime source text, user questions and
answer drafts are user-role data; they never replace developer instructions.
Run artifacts record the prompt version and SHA256 of the canonical instruction
mapping. Credentials and endpoint URLs never enter that mapping.
