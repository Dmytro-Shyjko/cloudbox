# Unintended `/delete` POST replay fix

## Root cause

`/files` accepted `POST` and rendered HTML directly on the same request (no PRG redirect). After a duplicate upload attempt, browser refresh/navigation could trigger form-resubmission flow. Because delete used plain `POST` forms to `/delete/...`, a stale submit/replay path could unintentionally delete a file.

## Fix summary

- Applied PRG on `/files`: every `POST` action now redirects to `GET /files?...`.
- Reworked file delete in `files.html` to non-submit button (`type="button"`) + explicit confirmation dialog.
- Added dedicated `files_delete.js` fetch flow with:
	- `X-CSRFToken` header
	- `X-Requested-With: XMLHttpRequest`
	- explicit payload (`path`, `filename`)
	- redirect on success via `GET`.
- Hardened `/delete/<path:filepath>`:
	- rejects non-AJAX requests
	- validates payload/path/filename match
	- resolves and anchors path under `storage/<user_id>/`
	- logs delete outcomes with `user_id` + `request_id`.
- Added regression tests for template safety, delete mismatch/traversal rejection, and chunked upload JS not containing `/delete` triggers.
