SHELL := /bin/bash
PY := .venv/bin/python
.PHONY: setup dev start worker build test e2e smoke model-smoke models backup restore types sample
setup:
	bash scripts/setup_macos.sh
dev:
	$(PY) scripts/run.py --dev
start:
	$(PY) scripts/run.py
worker:
	$(PY) -m app.workers.worker
build:
	npm run build --prefix apps/web
test:
	.venv/bin/ruff check apps/api scripts
	.venv/bin/pytest -q
	npm test --prefix apps/web
e2e:
	npm run e2e --prefix apps/web
smoke:
	bash scripts/smoke_test.sh
model-smoke:
	$(PY) scripts/model_smoke.py
real-smoke:
	$(PY) scripts/real_pipeline_smoke.py
models:
	bash scripts/download_models.sh all
backup:
	bash scripts/backup.sh
restore:
	@test -n "$(BACKUP)" || (echo 'Usage: make restore BACKUP=/absolute/path/backup.tar.gz'; exit 1)
	bash scripts/restore.sh "$(BACKUP)"
types:
	npm run types --prefix apps/web
sample:
	$(PY) scripts/create_sample_library.py --count 100
