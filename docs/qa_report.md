# CloudBox Test & Verification Report

Date: 2026-02-20  
Repo: `/workspace/cloudbox`

## 1) Scope and methodology

This sweep covered:
- local Python compile/test regression checks,
- static sanity checks for upload/delete frontend callsites,
- runtime/service verification commands for systemd/env/tmp-dir/cleanup,
- code-level production-readiness review for upload throttling, delete hardening, dedupe/cancel/cleanup logs,
- manual E2E verification plan with expected outcomes.

> Note: this execution environment is a container without `systemd` as PID 1 and without the production service user/env files. Service-level checks were still executed and recorded as environment-limited warnings.

## 2) Commands executed

### A. Local regression suite

```bash
python3 -m compileall -q app tests
pytest -q
pytest -q tests/test_chunked_uploads_api.py
pytest -q tests/test_delete_safety.py
pytest -q tests/test_uploads_cleanup_cli.py
```

### B. Static sanity checks

```bash
node -e "const fs=require('fs');['app/static/chunked_upload.js','app/static/files_delete.js'].forEach(f=>{new Function(fs.readFileSync(f,'utf8')); console.log('OK',f);});"
rg -n '/delete/' app/static || true
rg -n '/api/uploads' app/static
rg -n '<form[^>]*action=["'"'"'][^"'"'"']*/delete/' app/templates || true
```

### C. Runtime env + systemd verification

```bash
python3 --version
node --version
which systemctl
systemctl --version | head -n 2

systemctl status databoxpro-flask.service --no-pager
PID=$(systemctl show -p MainPID --value databoxpro-flask.service 2>/dev/null || true); echo "PID=$PID"; if [ -n "$PID" ] && [ "$PID" != "0" ] && [ -r "/proc/$PID/environ" ]; then tr '\0' '\n' < /proc/$PID/environ | rg 'UPLOAD_TMP_DIR|SECRET_KEY|UPLOAD_MAX_MB'; else echo 'Unable to inspect process environment (service unavailable).'; fi

ls -lah /var/lib/cloudbox/uploads_tmp || true
id dmytro || true
sudo -u dmytro bash -lc 'touch /var/lib/cloudbox/uploads_tmp/.qa_touch_test && rm -f /var/lib/cloudbox/uploads_tmp/.qa_touch_test' || true

systemctl status cloudbox-uploads-cleanup.timer --no-pager || true
systemctl list-timers | rg cloudbox || true
systemctl cat cloudbox-uploads-cleanup.service || true
journalctl -u cloudbox-uploads-cleanup.service -n 100 --no-pager || true

sudo -u dmytro bash -lc 'set -a; source /etc/cloudbox/cloudbox.env; set +a; cd ~/cloudbox; .venv/bin/flask --app run.py uploads cleanup --dry-run --ttl-hours 1'
UPLOAD_TMP_DIR=/tmp/cloudbox_uploads_tmp SECRET_KEY=abcdefghijklmnopqrstuvwxyz123456 UPLOAD_MAX_MB=1024 python3 -m flask --app run.py uploads cleanup --dry-run --ttl-hours 1
```

### D. Config/code audit checks

```bash
rg --files -g '*nginx*' -g '*.conf' -g '*.service' -g '*.timer'
rg -n "429|rate|limiter|uploads/init|cancel|dedupe|stale|delete" app tests
sed -n '1,220p' deploy/systemd/cloudbox.service
sed -n '1,220p' deploy/systemd/cloudbox-uploads-cleanup.service
sed -n '1,220p' deploy/systemd/cloudbox-uploads-cleanup.timer
rg -n "databoxpro-flask|cloudbox.service|127.0.0.1:5000|127.0.0.1:8000|cloudflare|nginx|UPLOAD_TMP_DIR" docs deploy app
```

## 3) Results summary

## Pass
- Python compile checks passed.
- Full pytest suite passed: `61 passed`.
- Targeted suites passed:
	- `tests/test_chunked_uploads_api.py`: `30 passed`
	- `tests/test_delete_safety.py`: `9 passed`
	- `tests/test_uploads_cleanup_cli.py`: `3 passed`
- JS parsing sanity passed for upload/delete scripts.
- Static callsite scan found `/api/uploads` calls only in `chunked_upload.js`; no direct `/delete/` string in static JS and no template form posting directly to `/delete/...`.
- Cleanup CLI command works when env vars are provided (32+ char `SECRET_KEY` etc.) in local runtime.

## Warning (environment-limited)
- systemd service/timer state could not be validated in this container (`Failed to connect to bus` / `System has not been booted with systemd as init system`).
- Production user/path checks for `dmytro` and `/var/lib/cloudbox/uploads_tmp` could not be completed (user/path absent here).
- `/etc/cloudbox/cloudbox.env` was not available for direct source in this environment.

## 4) Found issues, repro, evidence, and fixes

### Issue 1: Missing nginx deployment config in repo (cannot verify upload proxy limits)
- **Risk:** Medium (production chunked uploads can fail with 413/timeout/partial-body behavior).
- **Evidence:** no nginx config file found by repo scan.
- **Repro:** run `rg --files -g '*nginx*' -g '*.conf'` from repo root.
- **Recommended minimal fix:** add a documented nginx snippet (or committed template) containing:
	- `client_max_body_size` (at least max chunk size + headers margin),
	- `proxy_read_timeout` and `proxy_send_timeout` tuned for slow uplinks,
	- optional `proxy_request_buffering off;` for streamed chunk posts,
	- explicit route handling for `/api/uploads/` endpoints.

### Issue 2: Service naming/bind mismatch risk between production notes and repo template
- **Risk:** Medium.
- **Evidence:** provided target says `databoxpro-flask.service` on `127.0.0.1:5000`; repo template ships `cloudbox.service` binding `127.0.0.1:8000`.
- **Repro:** compare runtime unit (`systemctl cat databoxpro-flask.service`) with `deploy/systemd/cloudbox.service`.
- **Recommended minimal fix:** align one source of truth:
	1. either update production unit name/port to match template,
	2. or add `deploy/systemd/databoxpro-flask.service` reflecting actual production settings,
	3. and reference matching upstream in nginx config.

### Issue 3: Production verification gap for env inheritance and tmp-dir ownership
- **Risk:** High if misconfigured (falls back to wrong tmp location; cleanup/cancel behavior degrades).
- **Evidence in this environment:** cannot read service env due absent systemd bus/service user.
- **Recommended minimal fix on server:**
	- verify `EnvironmentFile=/etc/cloudbox/cloudbox.env` in active gunicorn unit,
	- verify `UPLOAD_TMP_DIR=/var/lib/cloudbox/uploads_tmp`,
	- ensure directory exists and is writable by service user,
	- validate with `flask uploads cleanup --dry-run` under service user.

## 5) Reliability/security posture notes (code audit)

- Upload init 429 behavior appears intentional (`maximum number of active uploads reached`) and frontend includes retry handling for 429/5xx.
- Cancel endpoint includes explicit decision logging and idempotent terminal states.
- Delete safety tests cover XHR/JSON/CSRF and trusted-click frontend behavior.
- Dedupe stale-entry handling is logged (`duplicate_ignored_stale`) and covered by existing tests.
- Cleanup CLI emits structured summary lines for observability.

## 6) Manual E2E verification checklist (expected outcomes)

These were prepared for execution on a live/staging host with nginx + gunicorn + Cloudflare tunnel:

1. Login/auth
	- Action: login with valid user.
	- Expected: redirected to files page, session active.
2. Files list load
	- Action: open files root + nested folder.
	- Expected: listing renders quickly, no JS errors.
3. Small upload (non-chunk)
	- Action: upload tiny file under legacy route.
	- Expected: file appears immediately.
4. Large upload (chunked)
	- Action: upload file above chunk threshold.
	- Expected: progress modal shows speed/ETA; completion auto-closes modal; file visible after refresh.
5. Duplicate in same folder
	- Action: upload same name twice.
	- Expected: overwrite prompt; cancel stops upload; confirm replaces file.
6. Cancel mid-upload
	- Action: cancel while chunks in progress.
	- Expected: immediate abort; cancel API 200; tmp chunks removed; localStorage state cleared; modal closes.
7. Resume after interruption
	- Action: drop network mid-upload, reload page.
	- Expected: resume prompt appears; status API returns missing chunks; upload resumes only missing parts.
8. Delete hardening
	- Action: valid delete via UI and invalid crafted requests.
	- Expected: valid delete succeeds; invalid attempts rejected and logged with reason.
9. Stale dedupe recovery
	- Action: seed stale `user_files.sha256` metadata with missing on-disk file, then upload same content.
	- Expected: stale record ignored/repaired; upload completes normally.

## 7) Recommended next actions (minimal)

1. Add nginx upload hardening template under `deploy/nginx/` and deploy it.
2. Normalize unit naming/port docs vs production.
3. Run the runbook (`docs/qa_runbook.md`) on the real host and attach command outputs as release evidence.
