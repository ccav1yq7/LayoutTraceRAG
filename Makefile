# Only implemented M0/M1/M2/M3/M4/M5 targets. No placeholder green targets for future milestones.
UV = uv run --locked --extra dev --extra storage --extra shopguide --extra shopguide-dev
.PHONY: lint test-unit test-integration test-qa test-agent test-api test-ecom test-ops web-build schema
lint:
	$(UV) ruff check shopguide tests/shopguide scripts/shopguide
	$(UV) mypy shopguide --ignore-missing-imports --follow-imports silent --check-untyped-defs
test-unit:
	$(UV) pytest tests/shopguide -q --junitxml=artifacts/shopguide/unit.xml
# 旧长视频 RAG 已舍弃并移出本仓库（保留在 CV 工作区的 LayoutTraceRAG/legacy/）。
schema:
	$(UV) shopguide schema --out docs/shopguide-contracts.json

test-integration:
	$(UV) pytest tests/shopguide/test_m2.py -q --junitxml=artifacts/shopguide/m2-integration.xml

test-qa:
	$(UV) pytest tests/shopguide/test_m3.py tests/shopguide/test_m3_benchmark.py -q --junitxml=artifacts/shopguide/m3-qa.xml

test-agent:
	$(UV) pytest tests/shopguide/test_m4.py -q --junitxml=artifacts/shopguide/m4/tests.xml

test-api:
	$(UV) pytest tests/shopguide/test_m5_api.py -q --junitxml=artifacts/shopguide/m5/api-tests.xml

web-build:
	npm --prefix web ci
	npm --prefix web run build

test-ecom:
	$(UV) pytest tests/shopguide/test_m6.py -q --junitxml=artifacts/shopguide/m6/tests.xml

test-ops:
	$(UV) pytest tests/shopguide/test_m7.py -q
