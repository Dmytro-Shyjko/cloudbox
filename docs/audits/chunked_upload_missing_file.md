# Chunked Upload Missing File Audit Report

## Observed symptom
Chunked upload `/api/uploads/<id>/complete` may return `{"ok": true}` while the final file is missing from both UI and disk. This indicates an integrity mismatch between upload completion response, filesystem state, and DB records.

## Root-cause candidates audited
1. **Missing post-replace verification**
	- Before this audit, `/complete` called `os.replace(temp_final, final_path)` and immediately inserted into `user_files` without verifying final existence/size.
	- If replace had a silent/environment edge failure, the endpoint could continue to DB completion logic.
2. **No hard DB/file invariant check**
	- DB record creation and upload `status='completed'` happened without a final defensive existence re-check.
3. **Cleanup deletion scope risk**
	- Temp cleanup in `cancel` and `complete` directly used `shutil.rmtree` on computed paths without explicit root anchoring checks.
4. **Path anchoring needed at completion boundary**
	- Final path is sanitized/resolved, but `/complete` lacked an explicit runtime assertion that the final path remains under user storage root.

## End-to-end /complete call graph and touched paths
- `complete_upload(upload_id)` in `app/api/uploads.py`
	- `_api_login_required()`
	- DB lock transition `uploads.status -> assembling`
	- `compute_missing_chunks(...)`
	- `resolve_chunk_upload_dir(user_id, upload_id)`
		- reads `UPLOAD_TMP_DIR_ABS/<user_id>/<upload_id>/chunks/*.part`
	- `resolve_final_file_path(...)`
		- computes destination under `<repo>/storage/<user_id>/<target_path>/<filename_final>`
	- Assembly writes to temp: `<final_name>.<upload_id>.uploading`
	- rename/move: `os.replace(temp_final, final_path)`
	- DB write: insert `user_files` row and mark `uploads.status='completed'`
	- cleanup: `_remove_upload_tmp_dir(user_id, upload_id, request_id)`

## DB/file consistency audit
- File table: `user_files` in migration/model layer (`path`, `filename`, `size`, `sha256` columns reflect disk-relative placement and metadata).
- `/complete` now enforces:
	1. replace success
	2. final file exists and is a file
	3. final size == bytes written == expected upload total_size
	4. only then write `user_files` and set upload completed
	5. defensive post-insert existence check; on mismatch upload is marked failed and 500 returned

## Deletion audit
Searched delete/move operations repository-wide (`rmtree`, `unlink`, `os.replace`, `shutil.move`, `Path.rename`) and specifically reviewed:
- `POST /api/uploads/<id>/cancel`
- `POST /api/uploads/<id>/complete`

Added guardrails:
- `_remove_upload_tmp_dir(...)` now refuses to delete when:
	- candidate path is outside `UPLOAD_TMP_DIR_ABS`, or
	- required `user_id` and `upload_id` path segments are not present.
- Guard violations log `ERROR` and skip deletion.

## Instrumentation added
Each `/complete` request now has `request_id=<uuid4>` correlation id, included in COMPLETE logs.

New high-signal logs include:
- begin context: upload_id/user_id/target_path/filenames/tmp_dir/chunk_dir/temp_final/final_path/expected_total_size
- missing chunk discovery
- missing chunk during assembly
- assembled summary: bytes_written + sha256_final
- replace failure details
- final missing/size mismatch
- DB/file mismatch
- cleanup guard refusal

## Operational note: journalctl commands
Use correlation id and COMPLETE prefix:

```bash
sudo journalctl -u databoxpro-flask.service -n 500 --no-pager | grep "<CORR_ID>"
sudo journalctl -u databoxpro-flask.service -n 500 --no-pager | grep "COMPLETE"
```

## If issue persists: next steps
1. Capture one failing request correlation id and compare COMPLETE log timeline (begin -> assembled -> replace/verify -> DB write).
2. Add one-shot filesystem snapshot logging for `final_path.parent` entries immediately after replace in production debug window.
3. If running multiple workers/hosts with shared DB but non-shared storage, verify storage mount consistency across workers.
