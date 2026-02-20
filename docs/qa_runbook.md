# CloudBox QA Runbook (Production Host)

Use this on the real server (Linux host with systemd + nginx + gunicorn).

## Environment baseline
- Repo path: `/home/dmytro/cloudbox`
- Gunicorn unit: `databoxpro-flask.service`
- Gunicorn bind: `127.0.0.1:5000`
- Env file: `/etc/cloudbox/cloudbox.env`
- Required tmp dir: `/var/lib/cloudbox/uploads_tmp`

Example safe env values (no secrets):
```bash
SECRET_KEY=replace-with-at-least-32-characters-minimum
UPLOAD_MAX_MB=1024
UPLOAD_TMP_DIR=/var/lib/cloudbox/uploads_tmp
```

## Production Evidence Checklist
Run each command and confirm expected output.

1) Service identity + bind
```bash
systemctl status databoxpro-flask.service --no-pager
systemctl cat databoxpro-flask.service
ss -ltnp | rg ':5000'
```
Expected:
- unit is `active (running)`.
- `--bind 127.0.0.1:5000` in ExecStart.
- listener on `127.0.0.1:5000`.

2) Runtime environment in process
```bash
PID=$(systemctl show -p MainPID --value databoxpro-flask.service)
echo "MainPID=$PID"
sudo tr '\0' '\n' < /proc/$PID/environ | rg 'SECRET_KEY|UPLOAD_MAX_MB|UPLOAD_TMP_DIR'
```
Expected:
- `UPLOAD_MAX_MB=1024`
- `UPLOAD_TMP_DIR=/var/lib/cloudbox/uploads_tmp`
- `SECRET_KEY=` exists and value length is >=32 chars.

3) Tmp directory ownership + writeability
```bash
ls -ld /var/lib/cloudbox/uploads_tmp
sudo -u dmytro bash -lc 'touch /var/lib/cloudbox/uploads_tmp/.qa_touch && rm -f /var/lib/cloudbox/uploads_tmp/.qa_touch'
```
Expected:
- directory exists.
- service user can write.

4) Confirm no fallback to `instance/...`
```bash
cd /home/dmytro/cloudbox
systemctl show -p MainPID --value databoxpro-flask.service
sudo tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value databoxpro-flask.service)/environ | rg '^UPLOAD_TMP_DIR='
sudo find /home/dmytro/cloudbox/instance -maxdepth 4 -type d -name uploads_tmp
```
Expected:
- runtime `UPLOAD_TMP_DIR` is only `/var/lib/cloudbox/uploads_tmp`.
- no active chunk directories under `/home/dmytro/cloudbox/instance/uploads_tmp`.

5) Cleanup service/timer
```bash
systemctl status cloudbox-uploads-cleanup.timer --no-pager
systemctl cat cloudbox-uploads-cleanup.service
journalctl -u cloudbox-uploads-cleanup.service -n 50 --no-pager
```
Expected:
- timer enabled and scheduled.
- cleanup service points to project venv + `uploads cleanup` command.
- recent cleanup summary logs.

6) Nginx upload proxy tuning
```bash
sudo nginx -T | rg -n 'cloudbox_uploads.conf|client_max_body_size|proxy_read_timeout|proxy_send_timeout|proxy_request_buffering|/api/uploads/'
```
Expected:
- include of `deploy/nginx/cloudbox_uploads.conf` (or copied equivalent).
- `client_max_body_size 1024m` (or same policy).
- explicit `/api/uploads/` location tuning.

## Nginx include instructions
1. Copy template:
```bash
sudo cp /home/dmytro/cloudbox/deploy/nginx/cloudbox_uploads.conf /etc/nginx/snippets/cloudbox_uploads.conf
```
2. Include in your `server {}`:
```nginx
include /etc/nginx/snippets/cloudbox_uploads.conf;
```
3. Validate + reload:
```bash
sudo nginx -t && sudo systemctl reload nginx
```

## `/api/uploads/init` 429 note
- In this app, 429 on init means active upload slots are exhausted (`MAX_ACTIVE_UPLOADS_PER_USER`), not a classic per-second rate limit.
- First actions:
	- finish/cancel active uploads,
	- run cleanup timer/service,
	- check for stale `initiated/uploading/assembling` rows.
- Tuning:
	- increase `MAX_ACTIVE_UPLOADS_PER_USER` carefully,
	- keep cleanup timer running,
	- avoid strict nginx `limit_req` on `/api/uploads/init`.
