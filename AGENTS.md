# AGENTS.md — CloudBox AI Rules

## Project Overview

CloudBox is a Flask-based file storage system.
The goal is to maintain stability, avoid breaking authentication and storage logic, and implement changes through controlled Pull Requests.

Stack:
- Python
- Flask
- Pytest
- Virtual environment (.venv)
- Git workflow: main / develop / feature/*

---

## Local Setup Instructions

To run locally:

1) Create virtual environment:
python3 -m venv .venv

2) Activate:
source .venv/bin/activate

3) Install dependencies:
pip install -r requirements.txt

4) Run:
python run.py

---

## Testing

Before creating a Pull Request:

pytest

All tests must pass.

If new logic is added, corresponding tests should be added in `/tests`.

---

## Formatting & Code Style

- Follow PEP8
- Use clear variable naming
- Avoid large functions
- Keep changes minimal

Before PR:
black .
pytest

---

## Git Workflow Rules

Never commit directly to `main`.

Branches:
- main → production-ready code
- develop → integration branch
- feature/* → new functionality

All changes must go through Pull Request.

---

## Critical Areas (Do Not Modify Without Clear Reason)

- Authentication logic
- Storage system (storage/)
- Configuration settings
- Database initialization logic

If changes are needed, explain reasoning clearly in PR.

---

## Commit Message Format

Use conventional style:

feat: add password reset flow
fix: correct file upload validation
docs: update README
refactor: improve storage handler
test: add upload edge-case tests

---

## Pull Request Requirements

PR must include:
- What was changed
- Why it was changed
- How to test it
- Confirmation that tests pass

---

## Development Philosophy

- Small changes
- One feature per branch
- Always test before merge
- Keep logic clean and modular
