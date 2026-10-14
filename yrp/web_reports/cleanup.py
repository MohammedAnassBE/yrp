import frappe
from frappe.utils import add_days, cint, now

RETENTION_DAYS = 14


def clear_old_jobs(limit=None, days=RETENTION_DAYS):
	"""Delete expired report jobs one transaction at a time."""

	query_limit = cint(limit) if limit is not None else 0
	jobs = frappe.get_all(
		"YRP Report Job",
		filters={"creation": ("<", add_days(now(), -cint(days)))},
		pluck="name",
		order_by="creation asc",
		limit=query_limit,
	)
	for job_name in jobs:
		_delete_job(job_name)


def _delete_job(job_name: str) -> bool:
	try:
		frappe.delete_doc("YRP Report Job", job_name, ignore_permissions=True, force=True)
		frappe.db.commit()
		return True
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Could not delete YRP Report Job {job_name}")
		return False
