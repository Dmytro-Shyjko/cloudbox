# Contributing to CloudBox

Thank you for contributing.

---

## Branch Strategy

Never work directly in main.

Development flow:

main → stable production
develop → integration
feature/* → new features

---

## Development Cycle

1) Switch to develop:
git checkout develop
git pull

2) Create feature branch:
git checkout -b feature/<feature-name>

3) Work and commit:
git add -A
git commit -m "feat: description"

4) Push branch:
git push -u origin feature/<feature-name>

5) Open Pull Request:
feature/* → develop

6) After testing:
develop → main

---

## Code Quality Rules

Before submitting PR:

black .
pytest

All tests must pass.

---

## Folder Structure

- app/ → application logic
- tests/ → automated tests
- storage/ → file storage system
- instance/ → configuration and runtime data

Keep responsibilities separated.

---

## Writing Tests

Every new feature must include:
- positive test
- edge case test
- failure case test (if applicable)

---

## Security Guidelines

- Never commit secrets
- Use .env for sensitive data
- Validate all user input
- Handle file uploads safely

---

## Pull Request Checklist

Before requesting review:

- Code formatted
- Tests pass
- No debug prints
- No commented unused code
- Clear commit messages
