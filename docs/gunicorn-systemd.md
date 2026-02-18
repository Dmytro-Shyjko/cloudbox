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
