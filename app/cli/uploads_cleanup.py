import uuid
from datetime import datetime, timedelta, timezone

import click
from flask import current_app
from flask.cli import AppGroup

from app.api.uploads import cleanup_upload_artifacts
from app.models import get_db


uploads_cli = AppGroup("uploads")
def _parse_db_datetime(value: str | None) -> datetime | None:
	if not value:
		return None
	for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
		try:
			dt = datetime.strptime(value, fmt)
			if dt.tzinfo is None:
				return dt.replace(tzinfo=timezone.utc)
			return dt.astimezone(timezone.utc)
		except ValueError:
			continue
	return None


@uploads_cli.command("cleanup")
@click.option("--ttl-hours", type=int, default=None, help="Expire uploads older than this TTL.")
@click.option("--dry-run", is_flag=True, default=False, help="Only log what would be cleaned.")
@click.option("--limit", type=int, default=200, show_default=True, help="Maximum number of stale uploads to process.")
def cleanup_uploads(ttl_hours: int | None, dry_run: bool, limit: int):
	if limit <= 0:
		raise click.BadParameter("--limit must be greater than 0")

	request_id = str(uuid.uuid4())
	now = datetime.now(timezone.utc)
	ttl = int(ttl_hours if ttl_hours is not None else current_app.config["UPLOAD_TTL_HOURS"])
	cutoff = now - timedelta(hours=ttl)
	cutoff_db = cutoff.strftime("%Y-%m-%d %H:%M:%S")

	summary = {
		"scanned": 0,
		"expired": 0,
		"chunks_deleted": 0,
		"files_deleted": 0,
		"tmp_dirs_deleted": 0,
		"skipped": 0,
		"failed": 0,
	}

	current_app.logger.info(
		"UPLOAD_CLEANUP_RUN request_id=%s ttl_hours=%s dry_run=%s limit=%s cutoff=%s",
		request_id,
		ttl,
		dry_run,
		limit,
		cutoff_db,
	)

	with get_db(current_app) as conn:
		candidates = conn.execute(
			"""
			SELECT id, user_id, status, target_path, filename_final, created_at, updated_at, last_activity_at
			FROM uploads
			WHERE status NOT IN ('completed', 'canceled', 'expired')
			AND COALESCE(last_activity_at, updated_at, created_at) <= ?
			ORDER BY COALESCE(last_activity_at, updated_at, created_at) ASC
			LIMIT ?
			""",
			(cutoff_db, limit),
		).fetchall()

		summary["scanned"] = len(candidates)

		for row in candidates:
			upload_id = row["id"]
			user_id = int(row["user_id"])
			status = str(row["status"])
			activity_dt = _parse_db_datetime(row["last_activity_at"] or row["updated_at"] or row["created_at"])
			if activity_dt is None:
				summary["skipped"] += 1
				current_app.logger.warning(
					"UPLOAD_CLEANUP_SKIP request_id=%s upload_id=%s user_id=%s status=%s decision=invalid_timestamp ttl_hours=%s",
					request_id,
					upload_id,
					user_id,
					status,
					ttl,
				)
				continue

			age_seconds = int((now - activity_dt).total_seconds())
			if age_seconds < (ttl * 3600):
				summary["skipped"] += 1
				current_app.logger.info(
					"UPLOAD_CLEANUP_SKIP request_id=%s upload_id=%s user_id=%s status=%s age_seconds=%s ttl_hours=%s decision=recent_activity",
					request_id,
					upload_id,
					user_id,
					status,
					age_seconds,
					ttl,
				)
				continue

			current_app.logger.info(
				"UPLOAD_CLEANUP_CANDIDATE request_id=%s upload_id=%s user_id=%s status=%s age_seconds=%s ttl_hours=%s decision=expire",
				request_id,
				upload_id,
				user_id,
				status,
				age_seconds,
				ttl,
			)

			try:
				cleanup_stats = {
					"chunks_deleted_count": 0,
					"files_deleted_count": 0,
					"tmp_dirs_deleted_count": 0,
				}
				if not dry_run:
					changed = conn.execute(
						"""
						UPDATE uploads
						SET status = 'expired', updated_at = ?
						WHERE id = ?
						AND user_id = ?
						AND status NOT IN ('completed', 'canceled', 'expired')
						AND COALESCE(last_activity_at, updated_at, created_at) <= ?
						""",
						(now.strftime("%Y-%m-%d %H:%M:%S"), upload_id, user_id, cutoff_db),
					).rowcount
					if changed == 0:
						summary["skipped"] += 1
						current_app.logger.info(
							"UPLOAD_CLEANUP_SKIP request_id=%s upload_id=%s user_id=%s status=%s age_seconds=%s ttl_hours=%s decision=race_or_updated",
							request_id,
							upload_id,
							user_id,
							status,
							age_seconds,
							ttl,
						)
						continue

				cleanup_stats = cleanup_upload_artifacts(
					conn=conn,
					upload_id=upload_id,
					user_id=user_id,
					target_path=row["target_path"] or "",
					filename_final=row["filename_final"] or "file",
					request_id=request_id,
					status_after_cleanup=None,
					delete_db_rows=True,
					delete_tmp_files=True,
					delete_assembled_temp=True,
					dry_run=dry_run,
				)

				if not dry_run:
					conn.commit()
				summary["expired"] += 1
				summary["chunks_deleted"] += int(cleanup_stats["chunks_deleted_count"])
				summary["files_deleted"] += int(cleanup_stats["files_deleted_count"])
				summary["tmp_dirs_deleted"] += int(cleanup_stats["tmp_dirs_deleted_count"])
				current_app.logger.info(
					"UPLOAD_CLEANUP_OK request_id=%s upload_id=%s user_id=%s status=%s age_seconds=%s ttl_hours=%s decision=%s deleted_counts=%s",
					request_id,
					upload_id,
					user_id,
					status,
					age_seconds,
					ttl,
					"dry_run" if dry_run else "expired",
					cleanup_stats,
				)
			except Exception as exc:
				summary["failed"] += 1
				conn.rollback()
				current_app.logger.exception(
					"UPLOAD_CLEANUP_FAIL request_id=%s upload_id=%s user_id=%s status=%s ttl_hours=%s decision=error error=%s",
					request_id,
					upload_id,
					user_id,
					status,
					ttl,
					exc,
				)

	current_app.logger.info(
		"UPLOAD_CLEANUP_SUMMARY request_id=%s scanned=%s expired=%s chunks_deleted=%s files_deleted=%s tmp_dirs_deleted=%s skipped=%s failures=%s ttl_hours=%s dry_run=%s",
		request_id,
		summary["scanned"],
		summary["expired"],
		summary["chunks_deleted"],
		summary["files_deleted"],
		summary["tmp_dirs_deleted"],
		summary["skipped"],
		summary["failed"],
		ttl,
		dry_run,
	)

	click.echo(
		"uploads cleanup summary: "
		f"scanned={summary['scanned']} "
		f"expired={summary['expired']} "
		f"chunks_deleted={summary['chunks_deleted']} "
		f"files_deleted={summary['files_deleted']} "
		f"tmp_dirs_deleted={summary['tmp_dirs_deleted']} "
		f"skipped={summary['skipped']} "
		f"failures={summary['failed']}"
	)



def init_uploads_cli(app):
	app.cli.add_command(uploads_cli)
