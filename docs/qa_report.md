# CloudBox QA Hardening Report

Date: 2026-02-20
Production repo path: `/home/dmytro/cloudbox`

## What was corrected
- Fixed environment references to production reality (`/home/dmytro/cloudbox`, `databoxpro-flask.service`, `127.0.0.1:5000`).
- Removed unsafe/short secret examples; all examples now require `SECRET_KEY` length >=32.
- Added an explicit Production Evidence Checklist with exact commands + expected outputs.
- Added chunk tmp-dir verification steps to prove writes happen only under `UPLOAD_TMP_DIR=/var/lib/cloudbox/uploads_tmp` and not under `instance/...` fallback.
- Added nginx upload template and inclusion guidance.
- Added production-matching systemd template (`deploy/systemd/databoxpro-flask.service`).

## 429 `/api/uploads/init` investigation
Finding:
- Client already guards init with `initInFlight` and upload session state.
- The observed 429 is primarily server-side slot exhaustion: init returns 429 when active uploads reach `MAX_ACTIVE_UPLOADS_PER_USER`.

Minimal fix implemented:
- During init, server now auto-marks expired active uploads as `expired` before active-count check.
- Active-count query now excludes expired rows.
- 429 message now explains slot usage and remediation (`cancel or finish existing uploads first`).

Operational meaning of 429 now:
- `maximum number of active uploads reached (X/Y)` means upload slots are full, not necessarily burst rate limiting.

## Production Evidence Checklist
Use `docs/qa_runbook.md` section “Production Evidence Checklist” directly.

Quick command index:
```bash
systemctl status databoxpro-flask.service --no-pager
systemctl cat databoxpro-flask.service
ss -ltnp | rg ':5000'
PID=$(systemctl show -p MainPID --value databoxpro-flask.service)
sudo tr '\0' '\n' < /proc/$PID/environ | rg 'SECRET_KEY|UPLOAD_MAX_MB|UPLOAD_TMP_DIR'
ls -ld /var/lib/cloudbox/uploads_tmp
sudo find /home/dmytro/cloudbox/instance -maxdepth 4 -type d -name uploads_tmp
systemctl status cloudbox-uploads-cleanup.timer --no-pager
sudo nginx -T | rg -n 'client_max_body_size|proxy_read_timeout|proxy_send_timeout|/api/uploads/'
```

Expected highlights:
- service active and bound to `127.0.0.1:5000`.
- `UPLOAD_TMP_DIR=/var/lib/cloudbox/uploads_tmp` in runtime env.
- no active chunk tmp usage under `/home/dmytro/cloudbox/instance/uploads_tmp`.
- cleanup timer enabled.
- nginx has upload directives and route-specific `/api/uploads/` tuning.
