# CloudBox Security Audit

## 1) Executive Summary

Scope reviewed: Flask application code in `app/`, templates, startup entrypoint, and repository-level docs/config. No Nginx, systemd unit, or Cloudflare Tunnel configuration files were present in the repository, so those controls are marked **Not verifiable from current code**.

Overall security posture:
- Core path traversal controls for per-user file storage are generally implemented correctly (`safe_rel_path` + `resolve_user_path` containment checks).
- Authentication/session hardening is currently weak and exposes account takeover/privilege escalation risk.
- CSRF protection is missing across state-changing forms.
- Upload controls exist but are incomplete for production DoS resilience.
- Operational hardening (secure cookies, production secrets, debug off, headers/proxy hardening) is incomplete.

---

## 2) Vulnerability List (Grouped by Severity)

### Critical

#### C-01: Hardcoded weak `SECRET_KEY` enables forged Flask sessions (admin role escalation)
**Risk**
Flask session integrity depends on `SECRET_KEY`. A known/default key lets an attacker forge signed session cookies and set `user_id`, `role=admin`, `is_premium`, bypassing authentication/authorization.

**Vulnerable code**
```python
app.config["SECRET_KEY"] = "change-me-in-production"
```
Location: `app/__init__.py`.

**Exact fix recommendation**
- Load `SECRET_KEY` from environment or secrets manager.
- Refuse startup when key is missing/weak in non-test environments.
- Rotate the key in production and invalidate all existing sessions.

**Patch suggestion**
```diff
diff --git a/app/__init__.py b/app/__init__.py
@@
+import os
@@
-    app.config["SECRET_KEY"] = "change-me-in-production"
+    secret = os.environ.get("SECRET_KEY", "")
+    if not secret and not test_config:
+        raise RuntimeError("SECRET_KEY must be set in environment")
+    app.config["SECRET_KEY"] = secret or "test-only-secret"
```

---

### High

#### H-01: CSRF protection is missing for state-changing POST routes (account/admin actions)
**Risk**
Cross-site requests can trigger authenticated actions (upload, delete, role changes, premium toggle, block/unblock, language changes) without user consent.

**Vulnerable code (representative)**
POST forms exist in templates with no CSRF token field and backend does not validate any token.

Examples:
- `app/templates/files.html` (mkdir/upload/rename/move/delete forms)
- `app/templates/admin_users.html` (premium/block/role forms)
- `app/templates/admin_user_detail.html` (premium/block/role/delete forms)
- `app/templates/base.html` (`/lang` POST form)

**Exact fix recommendation**
- Implement synchronizer token CSRF for all POST routes.
- Generate per-session token; inject to templates; verify token server-side before processing.
- Prefer Flask-WTF CSRFProtect if acceptable; otherwise lightweight custom token (no heavy deps).

**Patch suggestion (lightweight custom CSRF)**
```diff
diff --git a/app/__init__.py b/app/__init__.py
@@
+import secrets
+from flask import request, abort
@@
+    @app.context_processor
+    def inject_csrf():
+        if "csrf_token" not in session:
+            session["csrf_token"] = secrets.token_urlsafe(32)
+        return {"csrf_token": session["csrf_token"]}
+
+    @app.before_request
+    def enforce_csrf():
+        if request.method == "POST":
+            token = request.form.get("csrf_token", "")
+            if token != session.get("csrf_token"):
+                abort(400)
```
And add hidden field to every POST form:
```html
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">
```

#### H-02: `debug=True` in entrypoint risks debugger exposure / sensitive error leakage
**Risk**
If `run.py` is used in production by mistake, Werkzeug debugger can expose sensitive internals and potentially remote code execution in misconfigured deployments.

**Vulnerable code**
```python
if __name__ == "__main__":
    app.run(debug=True)
```
Location: `run.py`.

**Exact fix recommendation**
- Never hardcode debug mode on.
- Use environment flag defaulting to false.
- Ensure production uses WSGI server and systemd unit referencing non-debug startup.

**Patch suggestion**
```diff
diff --git a/run.py b/run.py
@@
+import os
@@
-if __name__ == "__main__":
-    app.run(debug=True)
+if __name__ == "__main__":
+    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
```

#### H-03: Missing secure session cookie policy (`Secure`, `HttpOnly`, `SameSite`) and lifetime controls
**Risk**
Default/implicit cookie settings can expose session cookies over non-HTTPS, increase theft via script access (if changed elsewhere), and enable CSRF/session abuse.

**Vulnerable code**
No explicit cookie hardening in app config (`SESSION_COOKIE_SECURE`, `SESSION_COOKIE_HTTPONLY`, `SESSION_COOKIE_SAMESITE`, `PERMANENT_SESSION_LIFETIME`).

**Exact fix recommendation**
Set secure defaults in app initialization.

**Patch suggestion**
```diff
diff --git a/app/__init__.py b/app/__init__.py
@@
+    app.config.update(
+        SESSION_COOKIE_SECURE=True,
+        SESSION_COOKIE_HTTPONLY=True,
+        SESSION_COOKIE_SAMESITE="Lax",
+        PERMANENT_SESSION_LIFETIME=3600,
+    )
```

#### H-04: Session fixation risk (session not rotated on login/register)
**Risk**
If attacker can set a victim session ID/cookie before authentication, victim login can bind to attacker-known session context.

**Vulnerable code (representative)**
Login/register sets authenticated keys but does not clear/regenerate session first.

**Exact fix recommendation**
- Call `session.clear()` before setting authenticated session data on successful login/auto-login.
- Recreate CSRF token after auth transition.

**Patch suggestion**
```diff
diff --git a/app/auth.py b/app/auth.py
@@
-    session["user_id"] = user["id"]
+    session.clear()
+    session["user_id"] = user["id"]
     session["username"] = user["username"]
     session["role"] = user.get("role", "user")
     session["is_premium"] = int(user.get("is_premium", 0))
```
(Apply to both `register_post` and `login_post` success blocks.)

#### H-05: Upload DoS risk due to missing global request body limit (`MAX_CONTENT_LENGTH`)
**Risk**
Per-file checks occur after body is received; very large uploads can still consume memory/disk/temp space and tie up workers.

**Vulnerable code**
No `MAX_CONTENT_LENGTH` configured in Flask app.

**Exact fix recommendation**
- Set global hard limit slightly above the largest paid plan upload.
- Enforce corresponding Nginx `client_max_body_size` (not verifiable from current code).

**Patch suggestion**
```diff
diff --git a/app/__init__.py b/app/__init__.py
@@
+    # Global request cap (e.g., 550 MB for 500 MB premium + overhead)
+    app.config["MAX_CONTENT_LENGTH"] = 550 * 1024 * 1024
```

---

### Medium

#### M-01: Login rate-limit can be bypassed via spoofed `X-Forwarded-For`
**Risk**
Code trusts client-supplied `X-Forwarded-For`; direct clients can rotate fake IPs and reduce effectiveness of brute-force protection.

**Vulnerable code**
```python
ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "0.0.0.0"
return ip.split(",")[0].strip()
```
Location: `app/auth.py`.

**Exact fix recommendation**
- Trust forwarded headers only from known reverse proxy chain.
- Otherwise use `request.remote_addr` only.
- If behind proxy, configure Werkzeug `ProxyFix` with strict hop count.

**Patch suggestion**
```diff
diff --git a/app/auth.py b/app/auth.py
@@
 def _client_ip() -> str:
-    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "0.0.0.0"
-    return ip.split(",")[0].strip()
+    return (request.remote_addr or "0.0.0.0").strip()
```
(Alternative: apply `ProxyFix` in app setup and read sanitized address.)

#### M-02: Logout is GET, enabling CSRF logout and cross-site state change via link/image
**Risk**
GET endpoints should be safe/idempotent. Cross-origin `<img src="/logout">` can log users out unexpectedly.

**Vulnerable code**
`@auth_bp.get("/logout")`

**Exact fix recommendation**
- Change logout to POST only and include CSRF token.
- Update navigation link to a small POST form button.

**Patch suggestion**
```diff
diff --git a/app/auth.py b/app/auth.py
@@
-@auth_bp.get("/logout")
+@auth_bp.post("/logout")
 def logout():
     session.clear()
     return redirect(url_for("auth.login_get"))
```

#### M-03: File type validation relies on extension + client-provided MIME (easily spoofed)
**Risk**
Attackers can upload disguised content (e.g., script renamed as image/pdf). If files are later served inline or processed, this can become XSS/RCE vector.

**Vulnerable code (representative)**
MIME check uses `f.mimetype` from request header, not trusted content inspection.

**Exact fix recommendation**
- Validate server-side magic bytes for critical file types (`pdf`, image signatures).
- Keep denylist/allowlist but add content sniffing before save.

**Patch suggestion (minimal, no heavy deps)**
```diff
diff --git a/app/routes.py b/app/routes.py
@@
+def _looks_like_pdf(head: bytes) -> bool:
+    return head.startswith(b"%PDF-")
+
+def _looks_like_png(head: bytes) -> bool:
+    return head.startswith(b"\x89PNG\r\n\x1a\n")
@@
+                                        head = f.stream.read(16)
+                                        f.stream.seek(0)
+                                        if ext == "pdf" and not _looks_like_pdf(head):
+                                            error = _t(lang, "err.upload_mime_mismatch")
```

---

### Low

#### L-01: Duplicate function definitions in `models.py` increase security maintenance risk
**Risk**
Repeated redefinitions (`list_users`, `toggle_user_*`, `set_user_role`, `delete_user`, `_ensure_users_columns`) make behavior order-dependent and error-prone. Security fixes can be applied to one definition but shadowed by later definitions.

**Vulnerable code**
Multiple duplicated definitions in `app/models.py`.

**Exact fix recommendation**
- Consolidate to single canonical implementation per function.
- Add unit tests around role changes/admin actions to ensure expected behavior.

**Patch suggestion**
Refactor `app/models.py` to remove duplicate defs and keep one authoritative block.

#### L-02: No explicit security headers configured in Flask (and Nginx config absent)
**Risk**
Missing HSTS, CSP, X-Frame-Options, X-Content-Type-Options can increase client-side attack surface.

**Vulnerable code**
No header middleware in Flask; Nginx configuration not present in repository.

**Exact fix recommendation**
- Set security headers in Nginx and optionally Flask fallback middleware.
- Verify HTTPS redirect and HSTS in edge/proxy.

**Patch suggestion (Flask fallback)**
```diff
diff --git a/app/__init__.py b/app/__init__.py
@@
+    @app.after_request
+    def add_security_headers(resp):
+        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
+        resp.headers.setdefault("X-Frame-Options", "DENY")
+        resp.headers.setdefault("Referrer-Policy", "no-referrer")
+        return resp
```

---

## 3) Immediate Fix Recommendations (Top 5 Actions)

1. Replace hardcoded `SECRET_KEY` with environment secret and rotate in production (invalidate sessions).
2. Implement CSRF protection across all POST endpoints and forms.
3. Disable hardcoded debug mode and enforce production startup path.
4. Configure secure session cookie settings and rotate session on login/register (`session.clear()` before setting auth keys).
5. Add global request size limit (`MAX_CONTENT_LENGTH`) and align with Nginx `client_max_body_size`.

---

## 4) Hardening Plan for Production Deployment

### Phase A: Application hardening (code)
- Secrets/config:
	- Move all secrets/limits to env-backed config.
	- Fail fast if production-critical settings are missing.
- Session/auth:
	- Secure cookie flags + session lifetime.
	- Session rotation on auth boundary.
	- POST-only logout + CSRF.
- Uploads:
	- Global request size limit.
	- Server-side file signature checks for allowed sensitive types.
	- Optional per-user upload rate limiting.
- Authorization:
	- Add tests for admin/moderator routes and self-protection constraints.

### Phase B: Reverse proxy / edge hardening
- Nginx:
	- `client_max_body_size`, timeouts, buffering limits.
	- Security headers: HSTS, CSP, X-Frame-Options, X-Content-Type-Options.
	- Enforce HTTPS redirect; block direct origin exposure where possible.
- Cloudflare Tunnel:
	- Restrict origin access to tunnel source only.
	- Enable WAF/rate-limit rules for login/upload endpoints.

### Phase C: Monitoring & incident readiness
- Add structured security logs for:
	- login failures/rate-limit triggers,
	- admin actions (role/premium/block/delete),
	- upload rejects and oversized request events.
- Alerting:
	- Excessive failed login attempts,
	- rapid admin action bursts,
	- disk usage thresholds.
- Backups:
	- Regular backup/restore drills for `instance/cloudbox.sqlite3` and `storage/`.

---

## Not Verifiable From Current Code
- Nginx directives: `client_max_body_size`, buffering/timeouts, headers, HTTPS enforcement.
- Cloudflare Tunnel ingress/ACL policy.
- systemd service hardening (`User=`, `NoNewPrivileges=`, `ProtectSystem=`, etc.).
- Runtime logging sink/rotation policy.
