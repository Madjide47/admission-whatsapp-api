# Makefile — API SaaS Admission WhatsApp
# Usage : make <commande>

PYTHON = venv\Scripts\python.exe
PYTEST = venv\Scripts\pytest.exe
UVICORN = venv\Scripts\uvicorn.exe

# ----------------------------------------------------------------------
# Développement
# ----------------------------------------------------------------------
.PHONY: run
run:
	$(PYTHON) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

.PHONY: run-port
run-port:
	$(PYTHON) -m uvicorn app.main:app --reload --host 127.0.0.1 --port $(PORT)

# ----------------------------------------------------------------------
# Base de données (SQLite dev)
# ----------------------------------------------------------------------
.PHONY: init-db
init-db:
	$(PYTHON) scripts/init_db_sqlite.py

# ----------------------------------------------------------------------
# Import Boussole.in
# ----------------------------------------------------------------------
.PHONY: import-boussole
import-boussole:
	$(PYTHON) scripts/import_boussole.py --file $(FILE)

.PHONY: import-boussole-dry
import-boussole-dry:
	$(PYTHON) scripts/import_boussole.py --file $(FILE) --dry-run

# Exemple : make import-boussole FILE=data/boussole_export.json

# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
.PHONY: test
test:
	$(PYTHON) -m pytest -ra -q

.PHONY: test-v
test-v:
	$(PYTHON) -m pytest -v

.PHONY: test-cov
test-cov:
	$(PYTHON) -m pytest --cov=app --cov-report=term-missing

# ----------------------------------------------------------------------
# Installation
# ----------------------------------------------------------------------
.PHONY: install
install:
	$(PYTHON) -m pip install -r requirements.txt

.PHONY: venv
venv:
	python -m venv venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt
