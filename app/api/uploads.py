import math
import hashlib
import os
import re
import sqlite3
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, session
from flask_wtf.csrf import validate_csrf
from wtforms.validators import ValidationError
from werkzeug.utils import secure_filename

from app.models import get_db
from app import csrf


uploads_api_bp = Blueprint("uploads_api", __name__, url_prefix="/api/uploads")


def validate_hex_sha256(value: str) -> bool:
	return bool(re.fullmatch(r"[0-9a-fA-F]{64}", (value or "").strip()))


def resolve_chunk_upload_dir(user_id: int, upload_id: str) -> Path:
	base = Path(current_app.config["UPLOAD_TMP_DIR_ABS"])
	return base / str(int(user_id)) / str(upload_id) / "chunks"


def compute_missing_chunks(total_chunks: int, uploaded_list: list[int]) -> list[int]:
	uploaded_set = set(uploaded_list)
	return [idx for idx in range(total_chunks) if idx not in uploaded_set]


def _json_error(message: str, status_code: int = 400, error_code: str | None = None):
	payload = {"error": error_code or message}
	if error_code:
		payload["message"] = message
	return jsonify(payload), status_code


def _validate_cancel_csrf() -> tuple[bool, str]:
	token = (request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or "").strip()
	if not token:
		return False, "csrf_required"
	try:
		validate_csrf(token)
	except ValidationError:
		return False, "csrf_invalid"
	return True, "ok"


def _api_login_required():
	uid = session.get("user_id")
	if not uid:
		return None, _json_error("authentication required", 401)
	return int(uid), None


def _normalize_target_path(value: str) -> str:
	path_raw = (value or "").replace("\\", "/").strip()
	path_raw = path_raw.lstrip("/")
	if not path_raw:
		return ""
	parts = [part for part in path_raw.split("/") if part]
	if any(part in (".", "..") for part in parts):
		raise ValueError("invalid target_path")
	clean_parts = [secure_filename(part) for part in parts]
	if any(not part for part in clean_parts):
		raise ValueError("invalid target_path")
	return "/".join(clean_parts)


def _to_iso8601(value: str | None) -> str | None:
	if not value:
		return None
	for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
		try:
			dt = datetime.strptime(value, fmt)
			if dt.tzinfo is None:
				dt = dt.replace(tzinfo=timezone.utc)
			return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
		except ValueError:
			continue
	return value


def _to_bool(value) -> bool:
	if isinstance(value, bool):
		return value
	if isinstance(value, (int, float)):
		return bool(value)
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "on")
	return False


def _is_chunk_path_safe(chunk_dir: Path, chunk_file: Path) -> bool:
	try:
		base_resolved = chunk_dir.resolve(strict=False)
		file_resolved = chunk_file.resolve(strict=False)
		return os.path.commonpath([str(base_resolved), str(file_resolved)]) == str(base_resolved)
	except (OSError, ValueError):
		return False


def resolve_user_storage_base(user_id: int) -> Path:
	return Path(current_app.root_path).parent / "storage" / str(int(user_id))


def resolve_final_file_path(user_id: int, target_path: str, filename_final: str) -> Path:
	base = resolve_user_storage_base(user_id)
	target_rel = _normalize_target_path(target_path)
	filename_clean = secure_filename(filename_final) or "file"
	final_path = (base / target_rel / filename_clean).resolve()
	base_resolved = base.resolve()
	if os.path.commonpath([str(base_resolved), str(final_path)]) != str(base_resolved):
		raise ValueError("invalid final path")
	return final_path


def _is_path_within(base: Path, candidate: Path) -> bool:
	try:
		base_resolved = base.resolve(strict=False)
		candidate_resolved = candidate.resolve(strict=False)
		return os.path.commonpath([str(base_resolved), str(candidate_resolved)]) == str(base_resolved)
	except (OSError, ValueError):
		return False


def _remove_upload_tmp_dir(user_id: int, upload_id: str, request_id: str | None = None) -> bool:
	upload_root_dir = resolve_chunk_upload_dir(user_id, upload_id).parent
	tmp_root = Path(current_app.config["UPLOAD_TMP_DIR_ABS"])
	if not _is_path_within(tmp_root, upload_root_dir):
		current_app.logger.error(
			"COMPLETE request_id=%s refused tmp cleanup outside root user_id=%s upload_id=%s tmp_root=%s candidate=%s",
			request_id or "-",
			user_id,
			upload_id,
			str(tmp_root.resolve(strict=False)),
			str(upload_root_dir.resolve(strict=False)),
		)
		return False

	parts = upload_root_dir.resolve(strict=False).parts
	if str(user_id) not in parts or upload_id not in parts:
		current_app.logger.error(
			"COMPLETE request_id=%s refused tmp cleanup missing required path segments user_id=%s upload_id=%s candidate=%s",
			request_id or "-",
			user_id,
			upload_id,
			str(upload_root_dir.resolve(strict=False)),
		)
		return False

	if upload_root_dir.exists():
		try:
			shutil.rmtree(upload_root_dir)
		except OSError as exc:
			current_app.logger.warning(
				"COMPLETE request_id=%s failed to remove upload temp dir user_id=%s upload_id=%s dir=%s error=%s",
				request_id or "-",
				user_id,
				upload_id,
				str(upload_root_dir),
				exc,
			)
			return False

	return True


def cleanup_upload_artifacts(
	conn,
	upload_id: str,
	user_id: int,
	target_path: str,
	filename_final: str,
	request_id: str | None = None,
	status_after_cleanup: str | None = "canceled",
	delete_db_rows: bool = True,
	delete_tmp_files: bool = True,
	delete_assembled_temp: bool = True,
	dry_run: bool = False,
) -> dict:
	chunks_deleted_count = 0
	files_deleted_count = 0
	tmp_dirs_deleted_count = 0
	now_db = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

	if delete_db_rows:
		chunk_count_row = conn.execute(
			"SELECT COUNT(*) AS c FROM upload_chunks WHERE upload_id = ?",
			(upload_id,),
		).fetchone()
		chunks_deleted_count = int(chunk_count_row["c"]) if chunk_count_row else 0
		if not dry_run:
			conn.execute("DELETE FROM upload_chunks WHERE upload_id = ?", (upload_id,))
	if status_after_cleanup is not None and not dry_run:
		conn.execute(
			"UPDATE uploads SET status = ?, updated_at = ?, last_activity_at = ? WHERE id = ? AND user_id = ?",
			(status_after_cleanup, now_db, now_db, upload_id, user_id),
		)

	upload_root_dir = resolve_chunk_upload_dir(user_id, upload_id).parent
	tmp_root = Path(current_app.config["UPLOAD_TMP_DIR_ABS"])
	can_remove_tmp = _is_path_within(tmp_root, upload_root_dir) and str(user_id) in upload_root_dir.resolve(strict=False).parts and upload_id in upload_root_dir.resolve(strict=False).parts
	if delete_tmp_files and can_remove_tmp and upload_root_dir.exists():
		try:
			for path in upload_root_dir.rglob("*"):
				if path.is_file():
					files_deleted_count += 1
					current_app.logger.debug(
						"CANCEL request_id=%s removing tmp file user_id=%s upload_id=%s path=%s",
						request_id or "-",
						user_id,
						upload_id,
						str(path.resolve(strict=False)),
					)
			if not dry_run:
				shutil.rmtree(upload_root_dir)
				tmp_dirs_deleted_count = 1
		except OSError as exc:
			current_app.logger.warning(
				"CANCEL request_id=%s failed tmp cleanup user_id=%s upload_id=%s dir=%s error=%s",
				request_id or "-",
				user_id,
				upload_id,
				str(upload_root_dir.resolve(strict=False)),
				exc,
			)
	elif delete_tmp_files and upload_root_dir.exists():
		current_app.logger.warning(
			"CANCEL request_id=%s skipped tmp cleanup outside root user_id=%s upload_id=%s tmp_root=%s candidate=%s",
			request_id or "-",
			user_id,
			upload_id,
			str(tmp_root.resolve(strict=False)),
			str(upload_root_dir.resolve(strict=False)),
		)

	if delete_assembled_temp:
		try:
			final_path = resolve_final_file_path(user_id, target_path or "", filename_final)
			parent_dir = final_path.parent
			candidate_paths = [
				final_path.with_name(f"{final_path.name}.{upload_id}.uploading"),
				final_path.with_name(f"{final_path.name}.{upload_id}.temp_final"),
				final_path.with_name(f"{final_path.name}.{upload_id}.tmp"),
			]
			for artifact_path in candidate_paths:
				if artifact_path.exists() and _is_path_within(parent_dir, artifact_path):
					try:
						if not dry_run:
							artifact_path.unlink(missing_ok=True)
						files_deleted_count += 1
						current_app.logger.debug(
							"CANCEL request_id=%s removing assembled artifact user_id=%s upload_id=%s path=%s",
							request_id or "-",
							user_id,
							upload_id,
							str(artifact_path.resolve(strict=False)),
						)
					except OSError as exc:
						current_app.logger.warning(
							"CANCEL request_id=%s failed assembled artifact cleanup user_id=%s upload_id=%s path=%s error=%s",
							request_id or "-",
							user_id,
							upload_id,
							str(artifact_path.resolve(strict=False)),
							exc,
						)
		except ValueError:
			current_app.logger.warning(
				"CANCEL request_id=%s skipped assembled artifact cleanup due to invalid path user_id=%s upload_id=%s target_path=%s filename_final=%s",
				request_id or "-",
				user_id,
				upload_id,
				target_path,
				filename_final,
			)

	return {
		"chunks_deleted_count": chunks_deleted_count,
		"files_deleted_count": files_deleted_count,
		"tmp_dirs_deleted_count": tmp_dirs_deleted_count,
	}


@uploads_api_bp.post("/init")
def init_upload():
	user_id, auth_error = _api_login_required()
	if auth_error:
		return auth_error

	data = request.get_json(silent=True) or {}
	filename_original = (data.get("filename") or "").strip()
	if not filename_original:
		return _json_error("filename is required")

	filename_final = secure_filename(filename_original) or "file"

	try:
		total_size = int(data.get("total_size"))
	except (TypeError, ValueError):
		return _json_error("total_size must be an integer")
	if total_size <= 0:
		return _json_error("total_size must be greater than 0")

	chunk_size_default = int(current_app.config["CHUNK_SIZE_DEFAULT_MB"]) * 1024 * 1024
	chunk_size_max = int(current_app.config["CHUNK_SIZE_MAX_MB"]) * 1024 * 1024

	if data.get("chunk_size") is None:
		chunk_size = chunk_size_default
	else:
		try:
			chunk_size = int(data.get("chunk_size"))
		except (TypeError, ValueError):
			return _json_error("chunk_size must be an integer")
	if chunk_size <= 0 or chunk_size > chunk_size_max:
		return _json_error("chunk_size out of bounds")

	derived_total_chunks = int(math.ceil(total_size / chunk_size))
	if data.get("total_chunks") is None:
		total_chunks = derived_total_chunks
	else:
		try:
			total_chunks = int(data.get("total_chunks"))
		except (TypeError, ValueError):
			return _json_error("total_chunks must be an integer")
		if total_chunks != derived_total_chunks:
			return _json_error("total_chunks is inconsistent with total_size/chunk_size")

	sha256_client = (data.get("sha256") or "").strip().lower()
	if sha256_client and not validate_hex_sha256(sha256_client):
		return _json_error("sha256 must be a 64-character hex value")

	if "target_folder_id" in data and data.get("target_folder_id") is not None:
		return _json_error("target_folder_id is not supported")

	if "target_path" not in data:
		return _json_error("target_path is required")

	try:
		target_path = _normalize_target_path(data.get("target_path"))
	except ValueError:
		return _json_error("invalid target_path")

	overwrite_requested = _to_bool(data.get("overwrite"))

	upload_id = str(uuid.uuid4())
	now_dt = datetime.now(timezone.utc)
	expires_at_dt = now_dt + timedelta(hours=int(current_app.config["UPLOAD_TTL_HOURS"]))
	now_db = now_dt.strftime("%Y-%m-%d %H:%M:%S")
	expires_at_db = expires_at_dt.strftime("%Y-%m-%d %H:%M:%S")

	with get_db(current_app) as conn:
		conn.execute(
			"""
			UPDATE uploads
			SET status = 'expired', updated_at = ?, last_activity_at = ?
			WHERE user_id = ?
				AND status IN ('initiated', 'uploading', 'assembling')
				AND expires_at IS NOT NULL
				AND expires_at <= ?
			""",
			(now_db, now_db, user_id, now_db),
		)

		existing_same_name = conn.execute(
			"""
			SELECT id
			FROM user_files
			WHERE user_id = ? AND path = ? AND filename = ?
			LIMIT 1
			""",
			(user_id, target_path, filename_final),
		).fetchone()
		if existing_same_name and not overwrite_requested:
			current_app.logger.info(
				"INIT refused file_exists user_id=%s path=%s filename=%s",
				user_id,
				target_path,
				filename_final,
			)
			return (
				jsonify(
					{
						"error": "file_exists",
						"message": "File already exists in target folder",
						"filename": filename_final,
						"path": target_path,
					}
				),
				409,
			)

		row = conn.execute(
			"""
			SELECT COUNT(*) AS c
			FROM uploads
			WHERE user_id = ?
				AND status IN ('initiated', 'uploading', 'assembling')
				AND (expires_at IS NULL OR expires_at > ?)
			""",
			(user_id, now_db),
		).fetchone()
		active_uploads = int(row["c"])
		max_active_uploads = int(current_app.config["MAX_ACTIVE_UPLOADS_PER_USER"])
		if active_uploads >= max_active_uploads:
			return _json_error(
				f"maximum number of active uploads reached ({active_uploads}/{max_active_uploads}); cancel or finish existing uploads first",
				429,
			)

		conn.execute(
			"""
			INSERT INTO uploads (
				id, user_id, target_path, filename_original, filename_final,
				total_size, chunk_size, total_chunks, status,
				sha256_client, overwrite_requested, created_at, updated_at, expires_at, last_activity_at
			)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			""",
			(
				upload_id,
				user_id,
				target_path,
				filename_original,
				filename_final,
				total_size,
				chunk_size,
				total_chunks,
				"initiated",
				sha256_client or None,
				1 if overwrite_requested else 0,
				now_db,
				now_db,
				expires_at_db,
				now_db,
			),
		)
		chunk_dir = resolve_chunk_upload_dir(user_id, upload_id)
		try:
			chunk_dir.mkdir(parents=True, exist_ok=True)
		except OSError:
			conn.rollback()
			return _json_error("failed to create upload temp directory", 500)
		conn.commit()

	return jsonify(
		{
			"upload_id": upload_id,
			"status": "initiated",
			"uploaded_chunks": [],
			"missing_chunks": list(range(total_chunks)),
			"expires_at": expires_at_dt.isoformat().replace("+00:00", "Z"),
		}
	)


@uploads_api_bp.get("/exists")
def file_exists_in_target_folder():
	user_id, auth_error = _api_login_required()
	if auth_error:
		return auth_error

	filename_original = (request.args.get("filename") or "").strip()
	if not filename_original:
		return _json_error("filename is required")

	filename_final = secure_filename(filename_original) or "file"
	try:
		target_path = _normalize_target_path(request.args.get("path"))
	except ValueError:
		return _json_error("invalid path")

	with get_db(current_app) as conn:
		row = conn.execute(
			"""
			SELECT id, filename, path, size, sha256, created_at
			FROM user_files
			WHERE user_id = ? AND path = ? AND filename = ?
			ORDER BY id DESC
			LIMIT 1
			""",
			(user_id, target_path, filename_final),
		).fetchone()

	if not row:
		return jsonify({"exists": False, "file": None})

	return jsonify(
		{
			"exists": True,
			"file": {
				"id": row["id"],
				"filename": row["filename"],
				"path": row["path"] or "",
				"size": row["size"],
				"sha256": row["sha256"],
				"created_at": _to_iso8601(row["created_at"]),
			},
		}
	)


@uploads_api_bp.get("/<upload_id>/status")
def upload_status(upload_id: str):
	user_id, auth_error = _api_login_required()
	if auth_error:
		return auth_error
	current_app.logger.debug("chunk upload status requested", extra={"upload_id": upload_id, "user_id": user_id})

	with get_db(current_app) as conn:
		upload_row = conn.execute(
			"SELECT id, status, total_chunks, total_size, chunk_size, expires_at FROM uploads WHERE id = ? AND user_id = ?",
			(upload_id, user_id),
		).fetchone()
		if not upload_row:
			return _json_error("upload not found", 404)

		total_chunks = int(upload_row["total_chunks"])
		if upload_row["status"] == "canceled":
			uploaded_chunks = []
			missing_chunks = list(range(total_chunks))
		else:
			chunk_rows = conn.execute(
				"SELECT chunk_index FROM upload_chunks WHERE upload_id = ? ORDER BY chunk_index ASC",
				(upload_id,),
			).fetchall()
			uploaded_chunks = [int(row["chunk_index"]) for row in chunk_rows]
			missing_chunks = compute_missing_chunks(total_chunks, uploaded_chunks)

		now_db = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
		conn.execute(
			"UPDATE uploads SET last_activity_at = ?, updated_at = ? WHERE id = ?",
			(now_db, now_db, upload_id),
		)
		conn.commit()

	return jsonify(
		{
			"upload_id": upload_id,
			"status": upload_row["status"],
			"total_chunks": total_chunks,
			"uploaded_chunks": uploaded_chunks,
			"missing_chunks": missing_chunks,
			"expected_total_size": int(upload_row["total_size"]),
			"chunk_size": int(upload_row["chunk_size"]),
			"expires_at": _to_iso8601(upload_row["expires_at"]),
		}
	)


@uploads_api_bp.post("/<upload_id>/chunk")
def upload_chunk(upload_id: str):
	user_id, auth_error = _api_login_required()
	if auth_error:
		return auth_error

	chunk_index_raw = (request.headers.get("X-Chunk-Index") or "").strip()
	if not chunk_index_raw:
		return _json_error("X-Chunk-Index header is required")
	try:
		chunk_index = int(chunk_index_raw)
	except ValueError:
		return _json_error("X-Chunk-Index must be an integer")

	chunk_size_header = request.headers.get("X-Chunk-Size")
	if chunk_size_header is not None and chunk_size_header.strip() != "":
		try:
			expected_size = int(chunk_size_header.strip())
		except ValueError:
			return _json_error("X-Chunk-Size must be an integer")
		if expected_size < 0:
			return _json_error("X-Chunk-Size must be non-negative")
	else:
		expected_size = None

	chunk_sha256_header = (request.headers.get("X-Chunk-SHA256") or "").strip().lower()
	if chunk_sha256_header and not validate_hex_sha256(chunk_sha256_header):
		return _json_error("X-Chunk-SHA256 must be a 64-character hex value")

	content_type = (request.content_type or "").split(";")[0].strip().lower()
	if content_type != "application/octet-stream":
		return _json_error("Content-Type must be application/octet-stream")

	body = request.get_data(cache=False, as_text=False)
	received_size = len(body)
	if received_size <= 0:
		return _json_error("chunk body must be greater than 0")

	chunk_size_max = int(current_app.config["CHUNK_SIZE_MAX_MB"]) * 1024 * 1024
	if received_size > chunk_size_max:
		return _json_error("chunk body exceeds maximum allowed size")

	if expected_size is not None and expected_size != received_size:
		return _json_error("X-Chunk-Size does not match received bytes")

	if chunk_sha256_header:
		chunk_sha256 = hashlib.sha256(body).hexdigest()
		if chunk_sha256 != chunk_sha256_header:
			return _json_error("chunk sha256 mismatch")
	else:
		chunk_sha256 = hashlib.sha256(body).hexdigest()

	now_db = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
	with get_db(current_app) as conn:
		upload_row = conn.execute(
			"""
			SELECT id, status, chunk_size, total_chunks, total_size
			FROM uploads
			WHERE id = ? AND user_id = ?
			""",
			(upload_id, user_id),
		).fetchone()
		if not upload_row:
			return _json_error("upload not found", 404)

		status = upload_row["status"]
		if status not in ("initiated", "uploading"):
			return _json_error("upload status does not allow chunk writes", 409)

		total_chunks = int(upload_row["total_chunks"])
		if chunk_index < 0 or chunk_index >= total_chunks:
			return _json_error("X-Chunk-Index out of range")

		declared_chunk_size = int(upload_row["chunk_size"])
		if chunk_index < (total_chunks - 1) and received_size != declared_chunk_size:
			return _json_error("non-last chunk size must match upload chunk_size")

		if chunk_index == (total_chunks - 1) and received_size > declared_chunk_size:
			return _json_error("last chunk size cannot exceed upload chunk_size")

		chunk_dir = resolve_chunk_upload_dir(user_id, upload_id)
		chunk_file = chunk_dir / f"{chunk_index}.part"
		tmp_file = chunk_dir / f"{chunk_index}.part.tmp"
		if not _is_chunk_path_safe(chunk_dir, chunk_file) or not _is_chunk_path_safe(chunk_dir, tmp_file):
			return _json_error("invalid chunk path", 400)

		chunk_dir.mkdir(parents=True, exist_ok=True)
		existing_row = conn.execute(
			"SELECT id, size FROM upload_chunks WHERE upload_id = ? AND chunk_index = ?",
			(upload_id, chunk_index),
		).fetchone()
		chunk_exists = chunk_file.exists()
		if existing_row and chunk_exists:
			existing_size = chunk_file.stat().st_size
			if int(existing_row["size"]) == received_size and existing_size == received_size:
				conn.execute(
					"UPDATE uploads SET last_activity_at = ?, updated_at = ? WHERE id = ?",
					(now_db, now_db, upload_id),
				)
				conn.commit()
				return jsonify({"ok": True, "upload_id": upload_id, "chunk_index": chunk_index, "received": received_size})
			return _json_error("chunk already exists with different metadata", 409)
		if existing_row or chunk_exists:
			return _json_error("chunk already exists with inconsistent state", 409)

		try:
			with tmp_file.open("wb") as fh:
				fh.write(body)
			os.replace(tmp_file, chunk_file)
		except OSError:
			if tmp_file.exists():
				tmp_file.unlink(missing_ok=True)
			return _json_error("failed to persist chunk", 500)

		try:
			conn.execute(
				"""
				INSERT INTO upload_chunks (upload_id, chunk_index, size, sha256)
				VALUES (?, ?, ?, ?)
				""",
				(upload_id, chunk_index, received_size, chunk_sha256),
			)
		except sqlite3.IntegrityError as exc:
			if "UNIQUE constraint failed" in str(exc):
				existing_row = conn.execute(
					"SELECT id, size FROM upload_chunks WHERE upload_id = ? AND chunk_index = ?",
					(upload_id, chunk_index),
				).fetchone()
				if existing_row and chunk_file.exists() and int(existing_row["size"]) == chunk_file.stat().st_size:
					conn.execute(
						"UPDATE uploads SET last_activity_at = ?, updated_at = ? WHERE id = ?",
						(now_db, now_db, upload_id),
					)
					conn.commit()
					return jsonify({"ok": True, "upload_id": upload_id, "chunk_index": chunk_index, "received": received_size})
			return _json_error("chunk already exists", 409)

		if status == "initiated":
			conn.execute(
				"UPDATE uploads SET status = 'uploading', updated_at = ?, last_activity_at = ? WHERE id = ?",
				(now_db, now_db, upload_id),
			)
		else:
			conn.execute(
				"UPDATE uploads SET updated_at = ?, last_activity_at = ? WHERE id = ?",
				(now_db, now_db, upload_id),
			)
		conn.commit()

	return jsonify({"ok": True, "upload_id": upload_id, "chunk_index": chunk_index, "received": received_size})


@uploads_api_bp.post("/<upload_id>/cancel")
@csrf.exempt
def cancel_upload(upload_id: str):
	user_id, auth_error = _api_login_required()
	if auth_error:
		return auth_error
	request_id = str(uuid.uuid4())
	csrf_present = bool((request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or "").strip())
	xhr_present = request.headers.get("X-Requested-With", "") == "XMLHttpRequest"
	csrf_ok, csrf_reason = _validate_cancel_csrf()
	if not csrf_ok:
		current_app.logger.info(
			"CANCEL_ATTEMPT request_id=%s user_id=%s upload_id=%s has_csrf=%s has_xhr=%s decision=rejected:%s chunks_deleted_count=0 files_deleted_count=0",
			request_id,
			user_id,
			upload_id,
			csrf_present,
			xhr_present,
			csrf_reason,
		)
		return _json_error("CSRF token is required", 400, csrf_reason)

	decision = "cancel_ok"
	status_response = "canceled"
	cleanup_stats = {
		"chunks_deleted_count": 0,
		"files_deleted_count": 0,
	}

	with get_db(current_app) as conn:
		upload_row = conn.execute(
			"SELECT id, status, target_path, filename_final FROM uploads WHERE id = ? AND user_id = ?",
			(upload_id, user_id),
		).fetchone()
		if not upload_row:
			current_app.logger.info(
				"CANCEL_ATTEMPT request_id=%s user_id=%s upload_id=%s has_csrf=%s has_xhr=%s decision=rejected:not_found chunks_deleted_count=0 files_deleted_count=0",
				request_id,
				user_id,
				upload_id,
				csrf_present,
				xhr_present,
			)
			return _json_error("upload not found", 404)

		status_now = str(upload_row["status"])
		status_after_cleanup = "canceled"
		if status_now in ("completed", "canceled"):
			decision = f"idempotent:{status_now}"
			status_response = status_now
			status_after_cleanup = None

		cleanup_stats = cleanup_upload_artifacts(
			conn=conn,
			upload_id=upload_id,
			user_id=user_id,
			target_path=upload_row["target_path"] or "",
			filename_final=upload_row["filename_final"],
			request_id=request_id,
			status_after_cleanup=status_after_cleanup,
		)
		conn.commit()

	current_app.logger.info(
		"CANCEL_ATTEMPT request_id=%s user_id=%s upload_id=%s has_csrf=%s has_xhr=%s decision=%s chunks_deleted_count=%s files_deleted_count=%s",
		request_id,
		user_id,
		upload_id,
		csrf_present,
		xhr_present,
		decision,
		cleanup_stats["chunks_deleted_count"],
		cleanup_stats["files_deleted_count"],
	)

	return jsonify({"ok": True, "status": status_response})


@uploads_api_bp.post("/<upload_id>/complete")
def complete_upload(upload_id: str):
	user_id, auth_error = _api_login_required()
	if auth_error:
		return auth_error
	request_id = str(uuid.uuid4())

	now_db = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
	with get_db(current_app) as conn:
		upload_row = conn.execute(
			"""
			SELECT id, status, total_chunks, target_path, filename_original, filename_final, total_size, overwrite_requested
			FROM uploads
			WHERE id = ? AND user_id = ?
			""",
			(upload_id, user_id),
		).fetchone()
		if not upload_row:
			return _json_error("upload not found", 404)

		status = upload_row["status"]
		if status == "assembling":
			return _json_error("already assembling", 409)
		if status not in ("initiated", "uploading"):
			return _json_error("upload status does not allow completion", 409)

		lock_result = conn.execute(
			"""
			UPDATE uploads
			SET status = 'assembling', updated_at = ?, last_activity_at = ?
			WHERE id = ? AND user_id = ? AND status IN ('initiated', 'uploading')
			""",
			(now_db, now_db, upload_id, user_id),
		)
		if lock_result.rowcount != 1:
			current_row = conn.execute(
				"SELECT status FROM uploads WHERE id = ? AND user_id = ?",
				(upload_id, user_id),
			).fetchone()
			if current_row and current_row["status"] == "assembling":
				return _json_error("already assembling", 409)
			return _json_error("upload status does not allow completion", 409)

		total_chunks = int(upload_row["total_chunks"])
		chunk_rows = conn.execute(
			"SELECT chunk_index FROM upload_chunks WHERE upload_id = ? ORDER BY chunk_index ASC",
			(upload_id,),
		).fetchall()
		uploaded_indices = [int(row["chunk_index"]) for row in chunk_rows]
		missing_chunks = compute_missing_chunks(total_chunks, uploaded_indices)

		chunk_dir = resolve_chunk_upload_dir(user_id, upload_id)
		for idx in range(total_chunks):
			chunk_file = chunk_dir / f"{idx}.part"
			if not chunk_file.exists():
				current_app.logger.error(
					"COMPLETE request_id=%s missing chunk file user_id=%s upload_id=%s chunk_index=%s chunk_path=%s",
					request_id,
					user_id,
					upload_id,
					idx,
					str(chunk_file.resolve(strict=False)),
				)
				missing_chunks.append(idx)

		missing_chunks = sorted(set(missing_chunks))
		if len(chunk_rows) != total_chunks or missing_chunks:
			current_app.logger.error(
				"COMPLETE request_id=%s missing chunks user_id=%s upload_id=%s total_chunks=%s uploaded_count=%s missing_chunks=%s chunk_dir=%s",
				request_id,
				user_id,
				upload_id,
				total_chunks,
				len(chunk_rows),
				missing_chunks,
				str(chunk_dir.resolve(strict=False)),
			)
			conn.execute(
				"UPDATE uploads SET status = ?, updated_at = ?, last_activity_at = ? WHERE id = ?",
				(status, now_db, now_db, upload_id),
			)
			conn.commit()
			return (
				jsonify(
					{
						"ok": False,
						"status": status,
						"missing_chunks": missing_chunks,
						"error": "missing_chunks",
					}
				),
				409,
			)

		try:
			storage_base = resolve_user_storage_base(user_id)
			final_path = resolve_final_file_path(
				user_id=user_id,
				target_path=upload_row["target_path"] or "",
				filename_final=upload_row["filename_final"],
			)
			if not _is_path_within(storage_base, final_path):
				raise ValueError("final path escaped user storage base")
		except ValueError:
			conn.execute(
				"UPDATE uploads SET status = 'failed', updated_at = ?, last_activity_at = ? WHERE id = ?",
				(now_db, now_db, upload_id),
			)
			conn.commit()
			current_app.logger.error(
				"COMPLETE request_id=%s invalid target path user_id=%s upload_id=%s target_path=%s filename_final=%s",
				request_id,
				user_id,
				upload_id,
				upload_row["target_path"],
				upload_row["filename_final"],
			)
			return _json_error("invalid target_path", 400)

		final_path.parent.mkdir(parents=True, exist_ok=True)
		temp_final = final_path.with_name(f"{final_path.name}.{upload_id}.uploading")
		hasher = hashlib.sha256()
		size_written = 0
		expected_total_size = int(upload_row["total_size"])
		current_app.logger.info(
			"COMPLETE request_id=%s begin user_id=%s upload_id=%s target_path=%s filename_original=%s filename_final=%s tmp_dir=%s chunk_dir=%s temp_final=%s final_path=%s expected_total_size=%s",
			request_id,
			user_id,
			upload_id,
			upload_row["target_path"] or "",
			upload_row["filename_original"],
			upload_row["filename_final"],
			str(Path(current_app.config["UPLOAD_TMP_DIR_ABS"]).resolve(strict=False)),
			str(chunk_dir.resolve(strict=False)),
			str(temp_final.resolve(strict=False)),
			str(final_path.resolve(strict=False)),
			expected_total_size,
		)

		try:
			with temp_final.open("wb") as dest:
				for idx in range(total_chunks):
					chunk_file = chunk_dir / f"{idx}.part"
					if not chunk_file.exists():
						current_app.logger.error(
							"COMPLETE request_id=%s missing chunk during assembly user_id=%s upload_id=%s chunk_index=%s chunk_path=%s",
							request_id,
							user_id,
							upload_id,
							idx,
							str(chunk_file.resolve(strict=False)),
						)
						raise FileNotFoundError(f"missing chunk during assembly: {chunk_file}")
					with chunk_file.open("rb") as src:
						while True:
							block = src.read(1024 * 1024)
							if not block:
								break
							dest.write(block)
							hasher.update(block)
							size_written += len(block)
				dest.flush()
				os.fsync(dest.fileno())

			sha256_final = hasher.hexdigest()
			overwrite_requested = bool(upload_row["overwrite_requested"])
			target_path = upload_row["target_path"] or ""
			target_filename = upload_row["filename_final"]
			current_app.logger.info(
				"COMPLETE request_id=%s assembled user_id=%s upload_id=%s bytes_written=%s sha256_final=%s",
				request_id,
				user_id,
				upload_id,
				size_written,
				sha256_final,
			)

			if overwrite_requested:
				existing_overwrite_row = conn.execute(
					"""
					SELECT id
					FROM user_files
					WHERE user_id = ? AND path = ? AND filename = ?
					ORDER BY id DESC
					LIMIT 1
					""",
					(user_id, target_path, target_filename),
				).fetchone()
				if existing_overwrite_row:
					deleted_file_id = int(existing_overwrite_row["id"])
					try:
						existing_disk_path = resolve_final_file_path(user_id, target_path, target_filename)
					except ValueError:
						existing_disk_path = None
					if existing_disk_path and existing_disk_path.exists() and existing_disk_path.is_file():
						existing_disk_path.unlink(missing_ok=True)
					conn.execute(
						"DELETE FROM user_files WHERE user_id = ? AND path = ? AND filename = ?",
						(user_id, target_path, target_filename),
					)
					current_app.logger.info(
						"COMPLETE overwrite removed existing file user_id=%s path=%s filename=%s deleted_file_id=%s",
						user_id,
						target_path,
						target_filename,
						deleted_file_id,
					)

			existing_rows = conn.execute(
				"SELECT id, path, filename FROM user_files WHERE user_id = ? AND sha256 = ? ORDER BY id ASC",
				(user_id, sha256_final),
			).fetchall()

			existing_file = None
			for existing_candidate in existing_rows:
				candidate_path = existing_candidate["path"] or ""
				candidate_filename = existing_candidate["filename"] or ""
				try:
					candidate_file_path = resolve_final_file_path(user_id, candidate_path, candidate_filename)
				except ValueError:
					current_app.logger.warning(
						"COMPLETE request_id=%s decision=duplicate_ignored_stale user_id=%s upload_id=%s filename=%s path=%s sha256=%s stale_file_id=%s stale_reason=invalid_path",
						request_id,
						user_id,
						upload_id,
						upload_row["filename_final"],
						upload_row["target_path"] or "",
						sha256_final,
						int(existing_candidate["id"]),
					)
					continue

				if candidate_file_path.exists() and candidate_file_path.is_file():
					existing_file = existing_candidate
					break

				current_app.logger.warning(
					"COMPLETE request_id=%s decision=duplicate_ignored_stale user_id=%s upload_id=%s filename=%s path=%s sha256=%s stale_file_id=%s stale_file_path=%s stale_reason=missing_file",
					request_id,
					user_id,
					upload_id,
					upload_row["filename_final"],
					upload_row["target_path"] or "",
					sha256_final,
					int(existing_candidate["id"]),
					str(candidate_file_path.resolve(strict=False)),
				)

			if existing_file:
				temp_final.unlink(missing_ok=True)
				file_id = int(existing_file["id"])
				duplicate = True
				current_app.logger.info(
					"COMPLETE request_id=%s decision=duplicate_used_existing user_id=%s upload_id=%s filename=%s path=%s sha256=%s existing_file_id=%s",
					request_id,
					user_id,
					upload_id,
					upload_row["filename_final"],
					upload_row["target_path"] or "",
					sha256_final,
					file_id,
				)
			else:
				if existing_rows:
					current_app.logger.info(
						"COMPLETE request_id=%s decision=duplicate_ignored_stale user_id=%s upload_id=%s filename=%s path=%s sha256=%s stale_candidates=%s",
						request_id,
						user_id,
						upload_id,
						upload_row["filename_final"],
						upload_row["target_path"] or "",
						sha256_final,
						len(existing_rows),
					)
				else:
					current_app.logger.info(
						"COMPLETE request_id=%s decision=missing_file user_id=%s upload_id=%s filename=%s path=%s sha256=%s",
						request_id,
						user_id,
						upload_id,
						upload_row["filename_final"],
						upload_row["target_path"] or "",
						sha256_final,
					)
				try:
					os.replace(temp_final, final_path)
				except OSError as exc:
					current_app.logger.error(
						"COMPLETE request_id=%s os.replace failed user_id=%s upload_id=%s temp_final=%s final_path=%s error=%s",
						request_id,
						user_id,
						upload_id,
						str(temp_final.resolve(strict=False)),
						str(final_path.resolve(strict=False)),
						exc,
					)
					raise

				if (not os.path.exists(final_path)) or (not final_path.is_file()):
					current_app.logger.error(
						"COMPLETE request_id=%s final file missing after replace user_id=%s upload_id=%s final_path=%s",
						request_id,
						user_id,
						upload_id,
						str(final_path.resolve(strict=False)),
					)
					raise OSError("final file missing after replace")

				final_size = final_path.stat().st_size
				if final_size != size_written or final_size != expected_total_size:
					current_app.logger.error(
						"COMPLETE request_id=%s final size mismatch user_id=%s upload_id=%s final_path=%s final_size=%s bytes_written=%s expected_total_size=%s",
						request_id,
						user_id,
						upload_id,
						str(final_path.resolve(strict=False)),
						final_size,
						size_written,
						expected_total_size,
					)
					raise OSError("final size mismatch")

				insert_result = conn.execute(
					"""
					INSERT INTO user_files (user_id, path, filename, size, sha256, created_at, updated_at)
					VALUES (?, ?, ?, ?, ?, ?, ?)
					""",
					(
						user_id,
						upload_row["target_path"] or "",
						upload_row["filename_final"],
						size_written,
						sha256_final,
						now_db,
						now_db,
					),
				)
				file_id = int(insert_result.lastrowid)
				duplicate = False

			if not duplicate and (not os.path.exists(final_path) or not final_path.is_file()):
				current_app.logger.error(
					"COMPLETE request_id=%s db/file mismatch after insert user_id=%s upload_id=%s final_path=%s",
					request_id,
					user_id,
					upload_id,
					str(final_path.resolve(strict=False)),
				)
				raise OSError("db/file mismatch after insert")

			conn.execute(
				"""
				UPDATE uploads
				SET status = 'completed', sha256_final = ?, updated_at = ?, last_activity_at = ?
				WHERE id = ?
				""",
				(sha256_final, now_db, now_db, upload_id),
			)
			conn.commit()

		except OSError as exc:
			temp_final.unlink(missing_ok=True)
			conn.execute(
				"UPDATE uploads SET status = 'failed', updated_at = ?, last_activity_at = ? WHERE id = ?",
				(now_db, now_db, upload_id),
			)
			conn.commit()
			current_app.logger.exception(
				"COMPLETE request_id=%s failed user_id=%s upload_id=%s temp_final=%s final_path=%s error=%s",
				request_id,
				user_id,
				upload_id,
				str(temp_final.resolve(strict=False)),
				str(final_path.resolve(strict=False)),
				exc,
			)
			return _json_error("failed to assemble upload", 500)

	_remove_upload_tmp_dir(user_id, upload_id, request_id=request_id)

	return jsonify(
		{
			"ok": True,
			"status": "completed",
			"file_id": str(file_id),
			"sha256": sha256_final,
			"duplicate": duplicate,
		}
	)
