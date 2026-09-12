# ShopGuide M0 data audit

Date: 2026-09-09. These are data/engineering checks, not benchmark model scores.

PM209 source is pinned at `AIM3-RUC/MPMQA` commit
`5226a9aa849fd3b8f35620e7b7d7d10c09d754a2`. Download followed the
[official README link](https://github.com/AIM3-RUC/MPMQA/blob/5226a9aa849fd3b8f35620e7b7d7d10c09d754a2/README.md).
The first transfer timed out after 240 seconds; a range request resumed it to completion.

Archive: `data/pm209/raw/PM209.zip`, 1,258,610,046 bytes.
SHA256: `8be9a8819f6e7474f3ecac6327198c1ec0292de6dc0cd06e2c13e69917238417`.
ZIP CRC check passed for all 10,442 members; total uncompressed size is
1,791,030,430 bytes. No original PDF files are present.

| Split | Manuals | Pages | Questions | Regions |
|---|---:|---:|---:|---:|
| train | 146 | 7,004 | 15,839 | 145,967 |
| val | 21 | 1,011 | 2,257 | 20,513 |
| test | 42 | 2,003 | 3,925 | 45,129 |
| Total | 209 | 10,018 | 22,021 | 211,609 |

Every referenced page image was opened and verified; all region boxes checked
against actual image dimensions. Missing images, invalid images, out-of-bounds or
zero-area boxes, duplicated within-page region IDs and dangling answer-region IDs:
**all zero**. Manual identity uses the image parent directory. Pairwise split
manual/image overlap: zero. QA strings were validated by the offline auditor;
no question text, answers or gold region lists were printed or copied to a runtime
corpus. M2 must implement corpus/gold separation before model inference.

Reproduce:

```bash
uv run --extra shopguide python scripts/shopguide/audit_pm209.py \
  --archive data/pm209/raw/PM209.zip --out artifacts/shopguide/m0/pm209-audit.json
```

ECom source is pinned at `XiaoduoAILab/ECom-Bench` commit
`bc5018daaf45eea12330941bce68cebd293cfa85`. AST inventory found **53 tasks,
21 registered MCP tools, 18 tasks with image URLs and 19 unique image URLs**.
The inventory records actual parameter names, data file counts and hashes without
exporting task instructions or hidden goals. One availability pass fetched and
verified 5/19 images; 14 failed network or image verification. This is a current
preflight failure, not proof of permanent loss. No affected task was removed.

```bash
uv run --extra shopguide python scripts/shopguide/audit_ecom.py \
  --root external/ECom-Bench --out artifacts/shopguide/m0/ecom-audit.json --check-images
```

The official ECom environment was **not run**: its model adapters/service wiring
remain separate from ShopGuide's Responses probe, and image preflight is incomplete.
Pinned ECom requires LangGraph 0.3.2 and LangChain Core 0.3.58; those requirements
were not installed into the application's newer environment. A separate reference
runtime and compatibility review remain required.

License review: both source repositories declare Apache-2.0 code licenses.
This does not grant general redistribution rights to product manuals, product
images, trademarks or external ECom photos. Dataset research-use and public-demo
permissions remain **pending**, and the formal-run manifest intentionally cannot
be marked approved. Synthetic fixtures are self-authored engineering inputs.
