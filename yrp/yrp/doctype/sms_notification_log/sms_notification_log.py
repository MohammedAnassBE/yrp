# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SMSNotificationLog(Document):
	pass


def create_sms_log(*, reference_doctype, reference_name, supplier, contact, mobile_no,
		result, send_path="Flow", template=None, template_name=None, template_id=None, message=""):
	"""Persist one SMS send attempt. `result` is a deliver_sms()/deliver_flow_sms()
	dict. `send_path` ("Flow" | "Legacy") records which sender was used so resend
	dispatches down the same path without inferring it. `template` links a
	Notification Template (legacy free-text path); `template_id`/`template_name`
	identify the MSG91 Flow template used (template-id path)."""
	log = frappe.get_doc({
		"doctype": "SMS Notification Log",
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"supplier": supplier,
		"contact": contact,
		"mobile_no": mobile_no,
		"send_path": send_path,
		"template": template,
		"template_name": template_name,
		"template_id": template_id,
		"message": message,
		"status": "Sent" if result.get("ok") else "Failed",
		"request_id": result.get("request_id"),
		"response_type": result.get("response_type"),
		"http_status": result.get("http_status"),
		"gateway_response": (result.get("raw") or "")[:500],
		"error": result.get("error"),
		"sent_at": frappe.utils.now_datetime(),
	})
	# ignore_links: this is an append-only audit log — it must never fail to
	# record a send attempt because the reference doc was deleted/renamed
	# after the fact (or, as in tests, is a placeholder that was never
	# inserted). Link validation would otherwise be enforced by
	# Document.insert() regardless of ignore_permissions.
	log.insert(ignore_permissions=True, ignore_links=True)
	return log
