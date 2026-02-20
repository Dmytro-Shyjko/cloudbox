# CloudBox QA Runbook (Fresh Server)

Use this on a staging/production-like Linux host where `systemd`, nginx, gunicorn, and Cloudflare tunnel are installed.

## Preconditions
- Repo deployed at `/opt/cloudbox`.
- Virtualenv exists at `/opt/cloudbox/.venv`.
- Env file exists at `/etc/cloudbox/cloudbox.env` (with placeholder-safe values in docs only).
- Service user/group (example: `cloudbox`) can write `UPLOAD_TMP_DIR`.

## 1) Baseline system info

```bash
python3 --version
node --version
nginx -v
systemctl --version | head -n 2
```

## 2) Local code regression checks

```bash
cd /opt/cloudbox
python3 -m compileall -q app tests
pytest -q
pytest -q tests/test_chunked_uploads_api.py
pytest -q tests/test_delete_safety.py
pytest -q tests/test_uploads_cleanup_cli.py
```

## 3) Static sanity checks

```bash
cd /opt/cloudbox
node -e "const fs=require('fs');['app/static/chunked_upload.js','app/static/files_delete.js'].forEach(f=>{new Function(fs.readFileSync(f,'utf8')); console.log('OK',f);});"
rg -n '/delete/' app/static || true
rg -n '/api/uploads' app/static
rg -n '<form[^>]*action=["'"'"'][^"'"'"']*/delete/' app/templates || true
```

## 4) Service env + tmp dir verification (critical)

```bash
PID=$(systemctl show -p MainPID --value databoxpro-flask.service)
echo "MainPID=$PID"
sudo tr '\0' '\n' < /proc/$PID/environ | grep -E 'UPLOAD_TMP_DIR|SECRET_KEY|UPLOAD_MAX_MB'

ls -lah /var/lib/cloudbox/uploads_tmp
sudo -u dmytro bash -lc 'touch /var/lib/cloudbox/uploads_tmp/.qa_touch && rm -f /var/lib/cloudbox/uploads_tmp/.qa_touch'
```

Expected:
- `UPLOAD_TMP_DIR=/var/lib/cloudbox/uploads_tmp` is present in process env.
- directory exists, writable, and not under `instance/...` fallback.

## 5) Cleanup timer/service verification

```bash
systemctl status cloudbox-uploads-cleanup.timer --no-pager
systemctl list-timers | grep cloudbox
systemctl cat cloudbox-uploads-cleanup.service
journalctl -u cloudbox-uploads-cleanup.service -n 100 --no-pager

sudo -u dmytro bash -lc 'set -a; source /etc/cloudbox/cloudbox.env; set +a; cd ~/cloudbox; .venv/bin/flask --app run.py uploads cleanup --dry-run --ttl-hours 1'
```

Expected:
- timer enabled and next run scheduled,
- cleanup summary lines in journal,
- dry-run succeeds and prints summary counters.

## 6) nginx/proxy verification

```bash
sudo nginx -T | rg -n 'client_max_body_size|proxy_read_timeout|proxy_send_timeout|proxy_request_buffering|/api/uploads|limit_req|429'
```

Minimum recommendations:
- `client_max_body_size` >= largest accepted chunk request.
- `proxy_read_timeout` and `proxy_send_timeout` generous for slow clients.
- if using strict rate-limits, exclude or tune `/api/uploads/init` to avoid poor UX.

## 7) Manual E2E flows

1. Login -> files list loads.
2. Upload small file (legacy path) -> immediate appearance.
3. Upload large file (chunked) -> modal progress + auto close on complete.
4. Duplicate upload in same folder -> overwrite prompt works.
5. Cancel mid-upload -> API 200, temp chunk data removed, UI closes.
6. Resume after network interruption -> missing chunks resumed only.
7. Delete invalid crafted request -> rejected and logged.
8. Stale dedupe scenario -> stale metadata ignored/recovered.

## 8) Evidence capture

Save outputs for release artifact:
- pytest summary,
- `systemctl`/`journalctl` outputs,
- nginx `-T` filtered snippet,
- short screen recording/screenshot of chunked upload + cancel/resume behavior.
