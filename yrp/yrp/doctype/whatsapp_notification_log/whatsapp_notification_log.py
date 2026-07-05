# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class WhatsAppNotificationLog(Document):
	pass


def create_whatsapp_log(*, reference_doctype, reference_name, supplier, contact, mobile_no,
		result, message_type="Template", template=None, template_name=None,
		language_code=None, message="", message_variables=None, header_source=None,
		account=None):
	"""Persist one WhatsApp send attempt.

	`result` is a deliver_whatsapp_template() dict (never-raise contract):
	{ok, meta_message_id, http_status, raw, error, meta_error, media_id,
	media_mime, file_name}. status is "Sent" only when the hub reported success
	AND a meta_message_id came back (result["ok"]); otherwise "Failed".

	`sent_at` is stamped ONLY on ok (deviation from create_sms_log's
	unconditional stamp) — a Failed row has no send time; the webhook later
	advances Sent -> Delivered -> Read by meta_message_id.

	`message_variables` ({header_vars, body_vars}) and `header_source`
	({header_format, source}) are stored as JSON so a resend can rebuild the
	send deterministically (media ids expire).
	"""
	ok = bool(result.get("ok"))
	# `raw` may arrive as a dict/list (a hub response echoed straight through)
	# or a string; normalize to a string before slicing so gateway_response
	# never crashes on a non-subscriptable value.
	raw_val = result.get("raw")
	raw_val = frappe.as_json(raw_val) if isinstance(raw_val, (dict, list)) else (raw_val or "")
	# meta_error (and, defensively, error) arrive as a dict/list on a failed Meta
	# send — the Meta error object. Stringify before storing in the Small Text
	# field, else the DB insert raises "dict can not be used as parameter".
	meta_error_val = result.get("meta_error")
	meta_error_val = frappe.as_json(meta_error_val) if isinstance(meta_error_val, (dict, list)) else meta_error_val
	error_val = result.get("error")
	error_val = frappe.as_json(error_val) if isinstance(error_val, (dict, list)) else error_val
	log = frappe.get_doc({
		"doctype": "WhatsApp Notification Log",
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"supplier": supplier,
		"contact": contact,
		"mobile_no": mobile_no,
		"account": account,
		"message_type": message_type,
		"template": template,
		"template_name": template_name,
		"language_code": language_code,
		"message": message,
		"message_variables": frappe.as_json(message_variables) if message_variables else None,
		"header_source": frappe.as_json(header_source) if header_source else None,
		"status": "Sent" if ok else "Failed",
		"meta_message_id": result.get("meta_message_id"),
		"http_status": result.get("http_status"),
		"gateway_response": raw_val[:500],
		"meta_error": meta_error_val,
		"error": error_val,
		"media_id": result.get("media_id"),
		"media_mime": result.get("media_mime"),
		"file_name": result.get("file_name"),
		"sent_at": frappe.utils.now_datetime() if ok else None,
	})
	# ignore_links: this is an append-only audit log — it must never fail to
	# record a send attempt because the reference / supplier / contact was
	# deleted or renamed after the fact (or, as in tests, is a placeholder that
	# was never inserted). Link validation would otherwise be enforced by
	# Document.insert() regardless of ignore_permissions.
	# ignore_permissions: written from server context on behalf of a user.
	log.insert(ignore_permissions=True, ignore_links=True)
	return log
