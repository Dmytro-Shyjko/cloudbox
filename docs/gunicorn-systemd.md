# Gunicorn systemd deployment note

Use Gunicorn (not the Flask development server) for production.

Recommended `ExecStart`:

```ini
ExecStart=/opt/cloudbox/.venv/bin/gunicorn --workers 2 --threads 4 --timeout 120 --bind 127.0.0.1:8000 --access-logfile - --error-logfile - "app:create_app()"
```

Notes:
- Keep bind on `127.0.0.1` because Nginx should proxy locally.
- Keep Flask debug and reloader disabled in production.
- Do not edit `/etc/systemd/system/*.service` from the repo; copy/update template and run `systemctl daemon-reload` on target host.

## Uploads cleanup timer

Chunked uploads that remain non-terminal past `UPLOAD_TTL_HOURS` can be expired with:

```bash
flask --app run.py uploads cleanup
```

Install timer units from repo templates:

```bash
sudo cp deploy/systemd/cloudbox-uploads-cleanup.service /etc/systemd/system/
sudo cp deploy/systemd/cloudbox-uploads-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cloudbox-uploads-cleanup.timer
sudo systemctl status cloudbox-uploads-cleanup.timer
```

Run manually any time:

```bash
sudo systemctl start cloudbox-uploads-cleanup.service
```
