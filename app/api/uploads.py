import math
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.utils import secure_filename

from app.models import get_db


uploads_api_bp = Blueprint("uploads_api", __name__, url_prefix="/api/uploads")


def validate_hex_sha256(value: str) -> bool:
	return bool(re.fullmatch(r"[0-9a-fA-F]{64}", (value or "").strip()))


def resolve_chunk_upload_dir(user_id: int, upload_id: str) -> Path:
	base = Path(current_app.config["UPLOAD_TMP_DIR_ABS"])
	return base / str(int(user_id)) / str(upload_id) / "chunks"


def compute_missing_chunks(total_chunks: int, uploaded_list: list[int]) -> list[int]:
	uploaded_set = set(uploaded_list)
	return [idx for idx in range(total_chunks) if idx not in uploaded_set]


def _json_error(message: str, status_code: int = 400):
	return jsonify({"error": message}), status_code


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

	upload_id = str(uuid.uuid4())
	now_dt = datetime.now(timezone.utc)
	expires_at_dt = now_dt + timedelta(hours=int(current_app.config["UPLOAD_TTL_HOURS"]))
	now_db = now_dt.strftime("%Y-%m-%d %H:%M:%S")
	expires_at_db = expires_at_dt.strftime("%Y-%m-%d %H:%M:%S")

	with get_db(current_app) as conn:
		row = conn.execute(
			"""
			SELECT COUNT(*) AS c
			FROM uploads
			WHERE user_id = ? AND status IN ('initiated', 'uploading', 'assembling')
			""",
			(user_id,),
		).fetchone()
		active_uploads = int(row["c"])
		if active_uploads >= int(current_app.config["MAX_ACTIVE_UPLOADS_PER_USER"]):
			return _json_error("maximum number of active uploads reached", 429)

		conn.execute(
			"""
			INSERT INTO uploads (
				id, user_id, target_path, filename_original, filename_final,
				total_size, chunk_size, total_chunks, status,
				sha256_client, created_at, updated_at, expires_at, last_activity_at
			)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


@uploads_api_bp.get("/<upload_id>/status")
def upload_status(upload_id: str):
	user_id, auth_error = _api_login_required()
	if auth_error:
		return auth_error

	with get_db(current_app) as conn:
		upload_row = conn.execute(
			"SELECT id, status, total_chunks, expires_at FROM uploads WHERE id = ? AND user_id = ?",
			(upload_id, user_id),
		).fetchone()
		if not upload_row:
			return _json_error("upload not found", 404)

		chunk_rows = conn.execute(
			"SELECT chunk_index FROM upload_chunks WHERE upload_id = ? ORDER BY chunk_index ASC",
			(upload_id,),
		).fetchall()
		uploaded_chunks = [int(row["chunk_index"]) for row in chunk_rows]
		missing_chunks = compute_missing_chunks(int(upload_row["total_chunks"]), uploaded_chunks)

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
			"uploaded_chunks": uploaded_chunks,
			"missing_chunks": missing_chunks,
			"expires_at": _to_iso8601(upload_row["expires_at"]),
		}
	)


@uploads_api_bp.post("/<upload_id>/cancel")
def cancel_upload(upload_id: str):
	user_id, auth_error = _api_login_required()
	if auth_error:
		return auth_error

	with get_db(current_app) as conn:
		upload_row = conn.execute(
			"SELECT id, status FROM uploads WHERE id = ? AND user_id = ?",
			(upload_id, user_id),
		).fetchone()
		if not upload_row:
			return _json_error("upload not found", 404)
		if upload_row["status"] == "completed":
			return _json_error("completed uploads cannot be canceled", 409)

		now_db = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
		conn.execute(
			"DELETE FROM upload_chunks WHERE upload_id = ?",
			(upload_id,),
		)
		conn.execute(
			"UPDATE uploads SET status = 'canceled', updated_at = ?, last_activity_at = ? WHERE id = ?",
			(now_db, now_db, upload_id),
		)
		conn.commit()

	chunk_dir = resolve_chunk_upload_dir(user_id, upload_id).parent
	if chunk_dir.exists():
		try:
			shutil.rmtree(chunk_dir)
		except OSError as exc:
			current_app.logger.warning("Failed to remove upload temp dir %s: %s", chunk_dir, exc)

	return jsonify({"ok": True, "status": "canceled"})
