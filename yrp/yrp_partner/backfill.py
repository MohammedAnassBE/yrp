"""Bounded Partner Type backfills using Frappe's worker transactions.

Small configurations stay synchronous. Large configurations queue after the Type
has committed, and each worker commits only one page through Frappe's normal job
runner. Saving the Type again supersedes older pages without erasing completed
partners. Membership reconciliation is idempotent, so a failed run can be restarted.
No worker creates accounts or changes Contact/User documents.
"""

from hashlib import sha256

import frappe
from frappe import _

INLINE_LIMIT = 100
BATCH_SIZE = 50


def _status_key(name):
	return "yrp_partner_backfill:" + name


def _status(name, revision, state, processed, total, after_commit=True):
	def publish():
		# A superseded job must not overwrite the newer configuration's status.
		if str(frappe.db.get_value("YRP Partner Type", name, "modified")) != revision:
			return
		value = {"partner_type": name, "revision": revision, "state": state,
			"processed": processed, "total": total}
		frappe.cache.set_value(_status_key(name), value, expires_in_sec=86400)
		frappe.publish_realtime("yrp_partner_backfill", value,
			room=f"user:{frappe.session.user}", user=frappe.session.user)
	if after_commit:
		frappe.db.after_commit.add(publish)
	else:
		publish()


def _enqueue(name, revision, cursor, processed, total):
	identity = sha256(f"{name}|{revision}|{cursor}".encode()).hexdigest()
	frappe.enqueue(
		"yrp.yrp_partner.backfill.run_batch", queue="long", timeout=600,
		enqueue_after_commit=True, job_id="yrp-partner-" + identity, deduplicate=True,
		partner_type=name, revision=revision, cursor=cursor, processed=processed, total=total,
		requested_by=frappe.session.user,
	)


def sync_partner_type(doc):
	"""Called from the validated Partner Type save; never commit inside its hook."""
	from yrp.yrp_partner.sync import ensure_partner

	total = frappe.db.count(doc.reference_doctype)
	if total <= INLINE_LIMIT:
		for name in frappe.get_all(doc.reference_doctype, pluck="name", order_by="name"):
			ensure_partner(doc.name, doc.reference_doctype, name)
		_status(doc.name, str(doc.modified), "Completed", total, total)
		return
	_status(doc.name, str(doc.modified), "Queued", 0, total)
	_enqueue(doc.name, str(doc.modified), "", 0, total)
	frappe.msgprint(_("Partner generation is queued in the background. Progress appears on this form."), alert=True)


def run_batch(partner_type, revision, cursor="", processed=0, total=0, requested_by=None):
	"""Recheck the actor/configuration, then process at most one ordered page."""
	from yrp.yrp_partner.sync import ensure_partner

	# Keep the initiating actor even when the framework retries a deadlock job
	# without forwarding its outer `user` argument.
	if requested_by:
		frappe.set_user(requested_by)
	if not frappe.db.exists("YRP Partner Type", partner_type):
		return
	doc = frappe.get_doc("YRP Partner Type", partner_type, for_update=True)
	if str(doc.modified) != revision:
		return
	committed_before = processed
	try:
		if not frappe.db.get_value("User", frappe.session.user, "enabled"):
			frappe.throw(_("The user who requested this backfill is disabled."), frappe.PermissionError)
		doc.check_permission("write")
		names = frappe.get_all(doc.reference_doctype, filters={"name": [">", cursor]},
			pluck="name", order_by="name", limit_page_length=BATCH_SIZE)
		for name in names:
			ensure_partner(doc.name, doc.reference_doctype, name)
		processed += len(names)
		more = len(names) == BATCH_SIZE
		_status(doc.name, revision, "Running" if more else "Completed", processed, total)
		if more:
			_enqueue(doc.name, revision, names[-1], processed, total)
	except Exception:
		# The worker logs the exception and rolls back this page. Keep a generic
		# visible failure state; no private Contact data is published to clients.
		_status(doc.name, revision, "Failed", committed_before, total, after_commit=False)
		raise


@frappe.whitelist()
def get_backfill_status(partner_type):
	"""Only users allowed to read the configuration may inspect its progress."""
	doc = frappe.get_doc("YRP Partner Type", partner_type)
	doc.check_permission("read")
	value = frappe.cache.get_value(_status_key(partner_type))
	return value if value and value.get("revision") == str(doc.modified) else None


@frappe.whitelist(methods=["POST"])
def restart_backfill(partner_type):
	"""Create a new configuration revision so failed/stalled runs can be retried."""
	doc = frappe.get_doc("YRP Partner Type", partner_type, for_update=True)
	doc.check_permission("write")
	doc.save()
