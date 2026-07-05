# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Manual supplier notifications from supplier-linked documents.

Ported from production_api.production_api.util.send_notification without the
auto-send / MRP Settings machinery (v1 is user-initiated only). The event is
derived from the document's docstatus; templates are Notification Template
records; the gateway is Frappe core's SMS Settings.
Spec: docs/superpowers/specs/2026-07-04-yrp-sms-supplier-notification-design.md
"""

import re

import frappe
from frappe import _

EVENT_BY_DOCSTATUS = {0: "Save", 1: "Submit", 2: "Cancel"}


def _parse_channels(channels):
	if isinstance(channels, str):
		channels = frappe.parse_json(channels) if channels.startswith("[") else [channels]
	return channels


def _get_doc_and_supplier(doctype, docname, supplier_key):
	doc = frappe.get_doc(doctype, docname)
	supplier_name = doc.get(supplier_key)
	if not supplier_name:
		frappe.throw(_("{0} {1} has no supplier to notify").format(_(doctype), docname))
	return doc, frappe.get_doc("Supplier", supplier_name)


@frappe.whitelist()
def send_notification(doctype: str, docname: str, channels="SMS", supplier_key: str = "supplier"):
	frappe.has_permission(doctype, ptype="write", doc=docname, throw=True)
	channels = _parse_channels(channels)
	if not channels:
		return
	doc, supplier = _get_doc_and_supplier(doctype, docname, supplier_key)
	supplier.send_notification(doctype, docname, channels, EVENT_BY_DOCSTATUS[doc.docstatus])


def _get_recipient_details(supplier):
	details = supplier.get_primary_contact_details()
	if not details["mobile"]:
		frappe.throw(_("Contact {0} has no primary mobile number").format(details["contact"]))
	return details


def _extract_numbers(contact_name, primary_mobile):
	"""Every distinct contact number, primary first. A single Contact Phone
	value may itself hold more than one number (comma/semicolon separated
	dirty data); split those too. Order is preserved, duplicates dropped."""
	rows = frappe.get_all("Contact Phone", filters={"parent": contact_name},
		fields=["phone", "is_primary_mobile_no"], order_by="idx")
	seen, ordered = set(), []

	def _add(num):
		num = (num or "").strip()
		if num and num not in seen:
			seen.add(num)
			ordered.append(num)

	# primary first
	for num in re.split(r"[,;]", primary_mobile or ""):
		_add(num)
	for r in rows:
		for num in re.split(r"[,;]", r.phone or ""):
			_add(num)
	return ordered


@frappe.whitelist()
def get_sms_context(doctype: str, docname: str, supplier_key: str = "supplier"):
	"""Everything the Send-SMS dialog needs: the supplier's recipient details
	and every enabled SMS template for the doctype, rendered for this doc.
	No event filter — manual sends are per-doctype (spec v1.1)."""
	frappe.has_permission(doctype, ptype="read", doc=docname, throw=True)
	doc, supplier = _get_doc_and_supplier(doctype, docname, supplier_key)
	details = _get_recipient_details(supplier)
	numbers = _extract_numbers(details["contact"], details["mobile"])

	template_names = frappe.get_all(
		"Notification Template",
		filters={"document_type": doctype, "channel": "SMS", "enabled": 1},
		pluck="name",
		order_by="name",
	)
	if not template_names:
		frappe.throw(_("No enabled SMS Notification Template for {0}").format(doctype))

	templates = []
	for name in template_names:
		template = frappe.get_doc("Notification Template", name)
		templates.append({"name": name, "message": template.get_message(docname=docname)})

	return {
		"supplier": supplier.name,
		"contact": details["contact"],
		"mobile": details["mobile"],
		"email": details["email"],
		"numbers": numbers,
		"templates": templates,
	}


@frappe.whitelist()
def send_sms_notification(
	doctype: str, docname: str, template: str, message: str | None = None,
	mobile_no: str | None = None, supplier_key: str = "supplier",
):
	"""Send one SMS template to the doc's supplier. A user-edited `message`
	overrides the rendered template text; the template's SMS Parameter rows
	(e.g. DLT ids) still apply. `mobile_no` picks which of the contact's
	numbers to send to (falls back to the contact's primary mobile). Every
	attempt — success or failure — is written to SMS Notification Log."""
	frappe.has_permission(doctype, ptype="write", doc=docname, throw=True)
	doc, supplier = _get_doc_and_supplier(doctype, docname, supplier_key)
	details = _get_recipient_details(supplier)
	number = (mobile_no or details["mobile"]).strip()

	template_doc = frappe.get_doc("Notification Template", template)
	if not template_doc.enabled or template_doc.channel != "SMS" or template_doc.document_type != doctype:
		frappe.throw(_("{0} is not an enabled SMS template for {1}").format(template, doctype))

	body = message
	if not body:
		body = template_doc.get_message(docname=docname)
	dynamic_params = [
		{"parameter": p.parameter, "value": p.value, "header": p.header}
		for p in (template_doc.parameters or [])
	]

	from yrp.sms import deliver_sms
	result = deliver_sms(body, number, dynamic_params)

	from yrp.yrp.doctype.sms_notification_log.sms_notification_log import create_sms_log
	create_sms_log(reference_doctype=doctype, reference_name=docname, supplier=supplier.name,
		contact=details["contact"], mobile_no=number, template=template, message=body,
		send_path="Legacy", result=result)

	if result["ok"]:
		_log_communication(doctype, docname, body, number)
		frappe.msgprint(_("SMS sent to {0}").format(number))
	else:
		frappe.msgprint(
			_("SMS to {0} failed: {1}").format(number, result.get("error") or result.get("http_status")),
			indicator="red",
		)


def _resolve_variable(token, doc, doctype, docname):
	"""Best-effort value for a template placeholder from the document context.
	{DocType}/{DocName}/{name} map to the doc identity; any other token is looked
	up as a field on the doc (dotted paths use the last segment). Unresolved
	tokens come back as "" so the user fills them in the popup."""
	low = token.lower()
	if low == "doctype":
		return doctype
	if low in ("docname", "name"):
		return docname
	field = token.split(".")[-1]
	value = doc.get(field)
	return value if value not in (None, "") else ""


@frappe.whitelist()
def get_flow_sms_context(doctype: str, docname: str, supplier_key: str = "supplier"):
	"""Everything the Send-SMS dialog needs for the MSG91 Flow (template-id)
	path: the supplier's recipient details, every contact number, and every
	template configured for this doctype in YRP SMS Settings — each with its
	{placeholder} variables pre-resolved from the document (unresolved ones are
	blank for the user to fill). MSG91 renders the message body from the
	template + these params, so no rendered text is returned."""
	frappe.has_permission(doctype, ptype="read", doc=docname, throw=True)
	doc, supplier = _get_doc_and_supplier(doctype, docname, supplier_key)
	details = _get_recipient_details(supplier)
	numbers = _extract_numbers(details["contact"], details["mobile"])

	from yrp.yrp.doctype.yrp_sms_settings.yrp_sms_settings import parse_template_variables
	settings = frappe.get_cached_doc("YRP SMS Settings")
	if not settings.enabled:
		frappe.throw(_("YRP SMS Settings is disabled"))
	rows = settings.get_templates_for_doctype(doctype)
	if not rows:
		frappe.throw(_("No SMS template configured for {0} in YRP SMS Settings").format(_(doctype)))

	templates = []
	for row in rows:
		variables = [
			{"name": token, "value": _resolve_variable(token, doc, doctype, docname)}
			for token in parse_template_variables(row.template_body)
		]
		templates.append({
			"name": row.template_name,
			"template_id": row.template_id,
			"body": row.template_body,
			"variables": variables,
		})

	return {
		"supplier": supplier.name,
		"contact": details["contact"],
		"mobile": details["mobile"],
		"email": details["email"],
		"numbers": numbers,
		"templates": templates,
		"doc_fields": _doc_field_options(doctype),
	}


def _doc_field_options(doctype):
	"""Selectable document fields for mapping a template placeholder to a field
	value in the Send SMS popup. Layout/table fields are excluded; `name` is
	offered explicitly."""
	from frappe.model import no_value_fields
	options = [{"value": "name", "label": _("Name (name)")}]
	for df in frappe.get_meta(doctype).fields:
		if df.fieldname and df.label and df.fieldtype not in no_value_fields and df.fieldtype != "Table":
			options.append({"value": df.fieldname, "label": f"{df.label} ({df.fieldname})"})
	return options


@frappe.whitelist()
def send_flow_sms_notification(
	doctype: str, docname: str, template_name: str, mobile_no: str | None = None,
	params=None, supplier_key: str = "supplier",
):
	"""Send one SMS to the doc's supplier via the MSG91 Flow API, using the
	template row named `template_name` for this doctype in YRP SMS Settings.
	`params` is the template's variable map (placeholder token -> value) filled
	in the popup. Every attempt is written to SMS Notification Log; a failure is
	surfaced as a red toast, not a throw, so the Failed row persists for resend."""
	frappe.has_permission(doctype, ptype="write", doc=docname, throw=True)
	doc, supplier = _get_doc_and_supplier(doctype, docname, supplier_key)
	details = _get_recipient_details(supplier)
	number = (mobile_no or details["mobile"]).strip()

	if isinstance(params, str):
		params = frappe.parse_json(params) if params.strip() else {}
	params = params or {}

	from yrp.sms import deliver_flow_sms
	result = deliver_flow_sms(reference_doctype=doctype, mobile_no=number, params=params,
		template_name=template_name)

	from yrp.yrp.doctype.sms_notification_log.sms_notification_log import create_sms_log
	create_sms_log(
		reference_doctype=doctype, reference_name=docname, supplier=supplier.name,
		contact=details["contact"], mobile_no=number, send_path="Flow",
		template_name=result.get("template_name") or template_name,
		template_id=result.get("template_id"),
		message=frappe.as_json(params) if params else "",
		result=result,
	)

	if result["ok"]:
		frappe.msgprint(_("SMS sent to {0} (request id {1})").format(number, result.get("request_id")))
	else:
		frappe.msgprint(
			_("SMS to {0} failed: {1}").format(number, result.get("error") or result.get("http_status")),
			indicator="red",
		)

	# Return the result so the /web SMS modal can gate on result.ok — a red
	# msgprint on an HTTP 200 is dropped by the SPA's fetch wrapper, so the
	# frontend must read the outcome from the return value. (Desk ignores it.)
	return result


def _log_communication(doctype, docname, message, number):
	from frappe.core.doctype.communication.email import _make as make_communication
	make_communication(doctype=doctype, name=docname, content=message, subject="SMS",
		sender="", recipients=number, communication_medium="SMS", send_email=False,
		communication_type="Automated Message")


@frappe.whitelist()
def resend_sms_notification_log(log_name):
	"""Re-send a previously logged SMS to the same number with the same
	message, updating that same log row in place."""
	log = frappe.get_doc("SMS Notification Log", log_name)
	# Guard the deleted-reference case: without this, an Administrator resend
	# would SEND the SMS and then roll back the status update when
	# _log_communication hits a dangling dynamic link (Communication.insert
	# has no ignore_links). Block before any send.
	if not frappe.db.exists(log.reference_doctype, log.reference_name):
		frappe.throw(_("Cannot resend: {0} {1} no longer exists").format(
			log.reference_doctype, log.reference_name))
	frappe.has_permission(log.reference_doctype, ptype="write", doc=log.reference_name, throw=True)

	is_flow = log.send_path == "Flow"
	if is_flow:
		from yrp.sms import deliver_flow_sms
		params = frappe.parse_json(log.message) if log.message else {}
		result = deliver_flow_sms(
			reference_doctype=log.reference_doctype, mobile_no=log.mobile_no, params=params,
			template_name=log.template_name)
	else:
		dynamic_params = []
		if log.template:
			template_doc = frappe.get_doc("Notification Template", log.template)
			dynamic_params = [
				{"parameter": p.parameter, "value": p.value, "header": p.header}
				for p in (template_doc.parameters or [])
			]
		from yrp.sms import deliver_sms
		result = deliver_sms(log.message, log.mobile_no, dynamic_params)

	log.status = "Sent" if result["ok"] else "Failed"
	log.request_id = result.get("request_id")
	log.response_type = result.get("response_type")
	log.http_status = result.get("http_status")
	log.gateway_response = (result.get("raw") or "")[:500]
	log.error = result.get("error")
	log.sent_at = frappe.utils.now_datetime()
	# ignore_links: like the original insert, an audit-log update must not fail
	# (and roll back a just-sent status) because a linked supplier/contact/
	# reference was deleted after the fact. save() takes it via the flag, not a kwarg.
	log.flags.ignore_links = True
	log.save(ignore_permissions=True)
	if result["ok"]:
		# Legacy free-text path logs a Communication; the Flow path stores only
		# the param map (not human-readable text), so it skips Communication.
		if not is_flow:
			_log_communication(log.reference_doctype, log.reference_name, log.message, log.mobile_no)
		frappe.msgprint(_("SMS resent to {0}").format(log.mobile_no))
	else:
		frappe.msgprint(
			_("Resend to {0} failed: {1}").format(log.mobile_no, result.get("error")),
			indicator="red",
		)
