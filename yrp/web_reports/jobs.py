import gzip
import hashlib
import json
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.core.doctype.prepared_report.prepared_report import (
	REPORT_TIMEOUT,
	create_json_gz_file,
	process_filters_for_prepared_report,
)
from frappe.desk.form.load import get_attachments
from frappe.desk.query_report import generate_report_result, get_report_doc
from frappe.desk.query_report import run as run_query_report
from frappe.utils import cint, flt, get_datetime, now_datetime, time_diff_in_hours
from frappe.utils.background_jobs import get_job_status, get_queue, get_workers

from yrp.web_reports.access import check_report_access
from yrp.web_reports.background import needs_background
from yrp.web_reports.filters import authorize_filters
from yrp.web_reports.registry import get_web_report, resolve_entry_path
from yrp.web_reports.stamp import auth_stamp

JOB_DOCTYPE = "YRP Report Job"
RESULT_MAX_AGE_HOURS = 24
LOST_JOB_GRACE_SECONDS = 120
PROGRESS_INTERVAL_SECONDS = 2
TERMINAL_STATUSES = {"Completed", "Error", "Expired"}
HEALTHY_RQ_STATUSES = {"queued", "deferred", "started"}
LOST_RQ_STATUSES = {"failed", "stopped", "canceled", "cancelled"}


def run_web_report(report_key: str, filters=None, refresh=0) -> dict:
	"""Validate and run an allow-listed report."""

	entry = get_web_report(report_key)
	check_report_access(entry)
	filters = authorize_filters(entry.filters, filters)
	_enqueue_lazy_cleanup()
	if not needs_background(entry, filters):
		return _sync_envelope(_run_sync(entry, filters))
	return _run_background(entry, filters, cint(refresh))


def get_report_job(report_key: str, job_name: str, include_result=0) -> dict:
	job, entry, _filters = _authorized_job(report_key, job_name)
	job = _expire_invalid_job(job, entry)
	if job.status not in TERMINAL_STATUSES:
		job = reconcile_report_job(job)
	if cint(include_result) and job.status == "Completed":
		try:
			payload = read_gzip_attachment(job)
		except frappe.ValidationError:
			job = _expire_job(job, _("Report result attachment is missing."))
		else:
			return _background_envelope(job, payload=payload)
	return _background_envelope(job)


def download_report_job(report_key: str, job_name: str):
	job, entry, _filters = _authorized_job(report_key, job_name)
	job = _expire_invalid_job(job, entry)
	if job.status != "Completed":
		frappe.throw(_("This report result is not available for download."))
	try:
		data, file_name = _read_gzip_attachment(job)
	except frappe.ValidationError:
		_expire_job(job, _("Report result attachment is missing."))
		frappe.throw(_("This report result is not available for download."))
	frappe.local.response.filename = file_name.removesuffix(".gz")
	frappe.local.response.filecontent = data
	frappe.local.response.type = "binary"


def execute_report_job(job: str) -> None:
	job_doc = frappe.get_doc(JOB_DOCTYPE, job)
	if not _transition(job, ("Queued",), "Started", started_at=now_datetime(), progress=0):
		return
	frappe.db.commit()
	try:
		entry = get_web_report(job_doc.report_key)
		filters = authorize_filters(entry.filters, job_doc.filters)
		check_report_access(entry)
		payload = _build_payload(job_doc, entry, filters)
		_validate_dependency_coverage(payload, _stamp_doctypes(job_doc))
		frappe.db.commit()
		if auth_stamp(job_doc.owner, entry, _stamp_doctypes(job_doc)) != job_doc.auth_stamp:
			_expire_started_job(job_doc)
			return
		_store_completed_result(job_doc, payload)
	except Exception:
		_finish_error(job_doc)


def reconcile_report_job(job):
	job = frappe.get_doc(JOB_DOCTYPE, job) if isinstance(job, str) else job
	if job.status not in {"Queued", "Started"}:
		return job
	status = _rq_status(job.rq_job_id)
	if status in HEALTHY_RQ_STATUSES:
		return job
	if status is None and job.status == "Queued" and _job_age_seconds(job) <= LOST_JOB_GRACE_SECONDS:
		return job
	is_missing_job = status is None and job.status in {"Queued", "Started"}
	if status not in LOST_RQ_STATUSES and not is_missing_job:
		return job
	if _transition(
		job.name,
		("Queued", "Started"),
		"Error",
		error_kind="lost",
		error_message=_("Background worker lost this report job."),
		finished_at=now_datetime(),
		active_key=None,
	):
		frappe.db.commit()
		_publish_done(job.owner, job.name, "Error")
	return frappe.get_doc(JOB_DOCTYPE, job.name)


def read_gzip_attachment(job):
	data, _file_name = _read_gzip_attachment(job)
	try:
		return json.loads(data)
	except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exception:
		raise frappe.ValidationError(_("The report result attachment is invalid.")) from exception


def _run_background(entry, filters: dict, refresh: int) -> dict:
	owner = frappe.session.user
	canonical_filters = process_filters_for_prepared_report(filters)
	stamp_doctypes = _resolve_stamp_doctypes(entry, filters)
	stamp = auth_stamp(owner, entry, stamp_doctypes)
	active_key = _active_key(owner, entry.key, canonical_filters, stamp)
	if active := _active_job(active_key):
		active = reconcile_report_job(active)
		if active.status in {"Queued", "Started"}:
			return _background_envelope(active)
	if not refresh and (reused := _reusable_job(owner, entry, canonical_filters, stamp)):
		return reused
	if not get_workers(queue=get_queue("long")):
		frappe.throw(_("background worker unavailable: no long worker is running."))
	job, is_new = _insert_job(entry, owner, canonical_filters, stamp, stamp_doctypes, active_key)
	if not is_new:
		if job.status == "Completed":
			return _background_envelope(job, payload=read_gzip_attachment(job), reused=True)
		return _background_envelope(job)
	try:
		frappe.enqueue(
			"yrp.web_reports.jobs.execute_report_job",
			queue="long",
			job_id=job.rq_job_id,
			enqueue_after_commit=True,
			timeout=_report_timeout(entry),
			job=job.name,
		)
	except Exception:
		frappe.db.rollback()
		raise
	return _background_envelope(job)


def _insert_job(entry, owner, filters, stamp, stamp_doctypes, active_key):
	name = frappe.generate_hash(length=20)
	job = frappe.get_doc(
		{
			"doctype": JOB_DOCTYPE,
			"name": name,
			"report_key": entry.key,
			"kind": entry.kind,
			"filters": filters,
			"auth_stamp": stamp,
			"status": "Queued",
			"rq_job_id": f"yrp_report|{name}",
			"active_key": active_key,
			"progress": 0,
			"stamp_doctypes": frappe.as_json(stamp_doctypes),
			"owner": owner,
		}
	)
	try:
		return job.insert(ignore_permissions=True), True
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		frappe.db.rollback()
		winner = _active_job(active_key) or _latest_matching_job(owner, entry.key, filters, stamp)
		if not winner:
			raise
		return winner, False


def _reusable_job(owner, entry, filters: str, stamp: str):
	cutoff = frappe.utils.add_to_date(now_datetime(), hours=-RESULT_MAX_AGE_HOURS)
	names = frappe.get_all(
		JOB_DOCTYPE,
		filters={
			"owner": owner,
			"report_key": entry.key,
			"filters": filters,
			"auth_stamp": stamp,
			"status": "Completed",
			"finished_at": (">", cutoff),
		},
		pluck="name",
		order_by="finished_at desc",
		limit=10,
	)
	for name in names:
		job = frappe.get_doc(JOB_DOCTYPE, name)
		try:
			payload = read_gzip_attachment(job)
		except frappe.ValidationError:
			_expire_job(job, _("Report result attachment is missing."))
			continue
		return _background_envelope(job, payload=payload, reused=True)
	return None


def _active_job(active_key: str):
	name = frappe.db.get_value(
		JOB_DOCTYPE,
		{"active_key": active_key, "status": ("in", ("Queued", "Started"))},
		"name",
	)
	return frappe.get_doc(JOB_DOCTYPE, name) if name else None


def _latest_matching_job(owner: str, report_key: str, filters: str, stamp: str):
	name = frappe.db.get_value(
		JOB_DOCTYPE,
		{"owner": owner, "report_key": report_key, "filters": filters, "auth_stamp": stamp},
		"name",
		order_by="creation desc",
	)
	return frappe.get_doc(JOB_DOCTYPE, name) if name else None


def _build_payload(job, entry, filters):
	if entry.kind == "frappe_report":
		report = get_report_doc(entry.report)
		report.disable_prepared_report_automation = 1
		return generate_report_result(report=report, filters=filters, user=job.owner)
	progress = _progress_callback(job)
	return resolve_entry_path(entry, "runner")(filters, progress=progress)


def _progress_callback(job):
	last_update = 0.0

	def update(current, total):
		nonlocal last_update
		now = time.monotonic()
		if now - last_update < PROGRESS_INTERVAL_SECONDS and flt(current) < flt(total):
			return
		last_update = now
		percent = 100 if not flt(total) else min(100, max(0, flt(current) / flt(total) * 100))
		frappe.db.set_value(JOB_DOCTYPE, job.name, "progress", percent, update_modified=False)
		frappe.db.commit()
		frappe.publish_realtime(
			"yrp_report_progress",
			{"job": job.name, "percent": percent},
			user=job.owner,
		)

	return update


def _store_completed_result(job, payload) -> None:
	request_user = frappe.session.user
	try:
		frappe.set_user("Administrator")
		create_json_gz_file(payload, JOB_DOCTYPE, job.name, job.report_key)
	finally:
		frappe.set_user(request_user)
	if not _transition_completed(
		job.name,
		progress=100,
		finished_at=now_datetime(),
		error_kind=None,
		error_message=None,
		active_key=None,
	):
		frappe.db.rollback()
		return
	frappe.db.commit()
	_publish_done(job.owner, job.name, "Completed")


def _finish_error(job) -> None:
	frappe.db.rollback()
	error = frappe.get_traceback(with_context=True)
	if _transition(
		job.name,
		("Started",),
		"Error",
		error_kind="failed",
		error_message=error,
		finished_at=now_datetime(),
		active_key=None,
	):
		frappe.db.commit()
		_publish_done(job.owner, job.name, "Error")


def _expire_started_job(job) -> None:
	if _transition(
		job.name,
		("Started",),
		"Expired",
		error_kind="expired",
		error_message=_("Permissions changed while the report was running."),
		finished_at=now_datetime(),
		active_key=None,
	):
		frappe.db.commit()
		_publish_done(job.owner, job.name, "Expired")


def _transition(job_name, statuses, destination, error_kind_required=None, **values) -> bool:
	assignments = {"status": destination, "modified": now_datetime(), **values}
	conditions = ["name = %s", "status IN %s"]
	parameters = [job_name, tuple(statuses)]
	if error_kind_required:
		conditions.append("error_kind = %s")
		parameters.append(error_kind_required)
	set_clause = ", ".join(f"`{fieldname}` = %s" for fieldname in assignments)
	frappe.db.sql(
		f"UPDATE `tab{JOB_DOCTYPE}` SET {set_clause} WHERE {' AND '.join(conditions)}",
		[*assignments.values(), *parameters],
	)
	return bool(frappe.db._cursor.rowcount)


def _transition_completed(job_name, **values) -> bool:
	assignments = {"status": "Completed", "modified": now_datetime(), **values}
	set_clause = ", ".join(f"`{fieldname}` = %s" for fieldname in assignments)
	frappe.db.sql(
		f"""UPDATE `tab{JOB_DOCTYPE}` SET {set_clause}
		WHERE name = %s AND (status = 'Started' OR (status = 'Error' AND error_kind = 'lost'))""",
		[*assignments.values(), job_name],
	)
	return bool(frappe.db._cursor.rowcount)


def _authorized_job(report_key: str, job_name: str):
	entry = get_web_report(report_key)
	check_report_access(entry)
	if not frappe.db.exists(JOB_DOCTYPE, job_name):
		frappe.throw(_("This report result is no longer available. Please rebuild it."), frappe.DoesNotExistError)
	job = frappe.get_doc(JOB_DOCTYPE, job_name)
	user = frappe.session.user
	if job.report_key != report_key or (job.owner != user and "System Manager" not in frappe.get_roles(user)):
		frappe.throw(_("You do not have access to this report job."), frappe.PermissionError)
	filters = authorize_filters(entry.filters, job.filters)
	return job, entry, filters


def _expire_invalid_job(job, entry):
	if job.status != "Completed":
		return job
	if _is_result_too_old(job):
		return _expire_job(job, _("This report result is older than 24 hours."))
	if auth_stamp(job.owner, entry, _stamp_doctypes(job)) != job.auth_stamp:
		return _expire_job(job, _("Permissions changed after this report was generated."))
	return job


def _expire_job(job, message):
	if _transition(
		job.name,
		("Completed",),
		"Expired",
		error_kind="expired",
		error_message=message,
		active_key=None,
	):
		frappe.db.commit()
		_publish_done(job.owner, job.name, "Expired")
	return frappe.get_doc(JOB_DOCTYPE, job.name)


def _validate_dependency_coverage(payload, stamp_doctypes) -> None:
	if not isinstance(payload, Mapping):
		return
	missing = set()
	for column in payload.get("columns") or []:
		if not isinstance(column, Mapping) or column.get("fieldtype") != "Link":
			continue
		options = column.get("options")
		if options and frappe.db.exists("DocType", options) and options not in stamp_doctypes:
			missing.add(options)
	if missing:
		frappe.throw(_("report dependency set incomplete: {0}").format(", ".join(sorted(missing))))


def _resolve_stamp_doctypes(entry, filters) -> list[str]:
	doctypes = set(entry.permission_doctypes or ())
	if entry.dependency_doctypes:
		doctypes.update(resolve_entry_path(entry, "dependency_doctypes")(filters) or ())
	return sorted(doctype for doctype in doctypes if doctype)


def _stamp_doctypes(job) -> list[str]:
	return list(frappe.parse_json(job.stamp_doctypes) or [])


def _read_gzip_attachment(job):
	job = frappe.get_doc(JOB_DOCTYPE, job) if isinstance(job, str) else job
	attachment = next((row for row in get_attachments(JOB_DOCTYPE, job.name) if _is_gzip(row)), None)
	if not attachment:
		frappe.throw(_("No gzip attachment found for this report job."))
	try:
		content = frappe.get_doc("File", attachment.name).get_content(encodings=())
	except Exception as exception:
		if not _is_missing_storage_object(exception):
			raise
		raise frappe.ValidationError(_("The report result attachment bytes are missing.")) from exception
	if isinstance(content, str):
		content = content.encode()
	try:
		return gzip.decompress(content), attachment.file_name
	except (gzip.BadGzipFile, EOFError, OSError, TypeError) as exception:
		raise frappe.ValidationError(_("The report result attachment is not valid gzip data.")) from exception


def _is_missing_storage_object(exception: Exception) -> bool:
	if isinstance(exception, (FileNotFoundError, frappe.DoesNotExistError)):
		return True
	response = getattr(exception, "response", {}) or {}
	error = response.get("Error", {}) if isinstance(response, Mapping) else {}
	return str(error.get("Code", "")) in {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}


def _is_gzip(attachment) -> bool:
	try:
		from frappe_s3_integration.prepared_report import _is_gzip_attachment

		return _is_gzip_attachment(attachment)
	except ImportError:
		file_name = str(attachment.get("file_name") or "").lower()
		path = urlsplit(str(attachment.get("file_url") or "")).path.lower()
		return file_name.endswith(".gz") or path.endswith(".gz")


def _run_sync(entry, filters):
	if entry.kind == "frappe_report":
		return run_query_report(
			entry.report,
			filters=filters,
			ignore_prepared_report=True,
			are_default_filters=True,
		)
	return resolve_entry_path(entry, "runner")(filters)


def _sync_envelope(payload) -> dict:
	return _result_envelope("sync", None, payload, reused=False)


def _background_envelope(job, payload=None, reused=False) -> dict:
	return _result_envelope("background", job, payload, reused=reused)


def _result_envelope(mode, job, payload, reused) -> dict:
	values = dict(payload) if isinstance(payload, Mapping) else {}
	if payload is not None and not isinstance(payload, Mapping):
		values["payload"] = payload
	values.update(
		{
			"mode": mode,
			"job": _job_payload(job) if job else None,
			"columns": values.get("columns") or [],
			"result": values.get("result") or [],
			"report_summary": values.get("report_summary"),
			"message": values.get("message"),
			"generated_at": job.finished_at if job else now_datetime(),
			"reused": bool(reused),
			"expired": bool(job and job.status == "Expired"),
		}
	)
	return values


def _job_payload(job) -> dict:
	return {
		"name": job.name,
		"status": job.status,
		"progress": flt(job.progress),
		"finished_at": job.finished_at,
	}


def _active_key(owner: str, report_key: str, filters: str, stamp: str) -> str:
	value = "\0".join((owner, report_key, filters, stamp))
	return hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest()


def _report_timeout(entry) -> int:
	if entry.kind == "frappe_report":
		return cint(frappe.db.get_value("Report", entry.report, "timeout")) or REPORT_TIMEOUT
	return REPORT_TIMEOUT


def _rq_status(job_id: str):
	status = get_job_status(job_id)
	return str(getattr(status, "value", status)).lower() if status is not None else None


def _job_age_seconds(job) -> float:
	return max(0, (now_datetime() - get_datetime(job.creation)).total_seconds())


def _is_result_too_old(job) -> bool:
	generated_at = job.finished_at or job.creation
	return time_diff_in_hours(now_datetime(), get_datetime(generated_at)) >= RESULT_MAX_AGE_HOURS


def _publish_done(user: str, job_name: str, status: str) -> None:
	frappe.publish_realtime("yrp_report_done", {"job": job_name, "status": status}, user=user)


def _enqueue_lazy_cleanup() -> None:
	try:
		key = frappe.cache.make_key("yrp_report_cleanup_hourly")
		if not frappe.cache.set(key, "1", ex=3600, nx=True):
			return
		frappe.enqueue(
			"yrp.web_reports.cleanup.clear_old_jobs",
			queue="short",
			job_id="yrp_report_cleanup",
			deduplicate=True,
			limit=50,
		)
	except Exception:
		frappe.log_error(title="Could not enqueue YRP report cleanup")
