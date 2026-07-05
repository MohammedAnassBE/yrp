# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

"""Single-number SMS delivery that captures the gateway response so a send can
be logged and traced. Reuses the SMS Settings machinery in the Notification
Template sender fork. Spec: docs/superpowers/specs/2026-07-04-yrp-sms-supplier-notification-design.md
"""

import frappe
from frappe import _
from yrp.yrp.doctype.notification_template.notification_template import (
	validate_receiver_nos,
)
from yrp.yrp.doctype.yrp_sms_settings.yrp_sms_settings import get_sms_config


def deliver_flow_sms(*, reference_doctype: str, mobile_no: str, params=None,
		template_name: str | None = None) -> dict:
	"""Send one SMS via the MSG91 Flow API, using the template_id + authkey
	configured for `reference_doctype` (optionally the row named `template_name`)
	in YRP SMS Settings.

	Returns {ok, http_status, request_id, response_type, template_id,
	template_name, raw, error}. `ok` is True only when the HTTP status is 2xx AND
	MSG91's JSON body reports `type == "success"` — MSG91 returns HTTP 200 with
	`{"type": "error"}` for rejected sends, so the status code alone is not proof
	of acceptance. `template_id`/`template_name` echo the row actually used, so
	the caller can stamp the log from a single source of truth. Never raises:
	any failure is captured in the returned dict."""
	result = {"ok": False, "http_status": None, "request_id": None,
		"response_type": None, "template_id": None, "template_name": None,
		"raw": "", "error": None}
	try:
		cfg = get_sms_config(reference_doctype, template_name)
		result["template_id"] = cfg["template_id"]
		result["template_name"] = cfg["template_name"]
		number = _normalise_number(mobile_no, cfg["country_code"])
		captured = _send_flow(cfg["gateway_url"], cfg["authkey"], cfg["template_id"], number, params or {})
		result.update(captured)
	except Exception as e:
		result["error"] = str(e)
		frappe.log_error(frappe.get_traceback(), "deliver_flow_sms failed")
	return result


def _normalise_number(mobile_no, country_code):
	"""Strip formatting, drop a leading domestic 0, and prefix the country code
	to a bare 10-digit number. Numbers that already carry a country code
	(len > 10 after the 0 is dropped) are left untouched."""
	num = (mobile_no or "").strip()
	for x in [" ", "-", "(", ")", "+"]:
		num = num.replace(x, "")
	# strip a single leading domestic 0 (e.g. 09944405056 -> 9944405056)
	if len(num) == 11 and num.startswith("0"):
		num = num[1:]
	if not num:
		frappe.throw(_("Please enter a valid mobile no"))
	cc = (country_code or "").strip()
	if cc and len(num) == 10:
		num = cc + num
	return num


def _send_flow(gateway_url, authkey, template_id, mobile, params):
	"""Replicates the proven F15 essdee_attendance MSG91 call byte-for-byte: a
	GET to the configured endpoint with authkey + mobile + template_id (and any
	template variables) as query params. MSG91 returns a JSON body carrying
	`type` (success|error) even on HTTP 200."""
	import requests

	query = {"authkey": authkey, "mobile": mobile, "template_id": template_id}
	for key, value in (params or {}).items():
		query[key] = value
	headers = {"Content-Type": "application/json", "Accept": "application/json"}

	response = requests.get(gateway_url, params=query, headers=headers, timeout=30)
	raw = (response.text or "").strip()
	status = response.status_code
	response_type, request_id, error = None, None, None
	try:
		data = response.json()
		response_type = data.get("type")
		if response_type == "success":
			# OTP endpoint returns the id in `request_id`; Flow returns it in `message`.
			request_id = data.get("request_id") or data.get("message")
		else:
			error = data.get("message") or raw
	except ValueError:
		error = raw or _("Non-JSON gateway response")
	ok = (200 <= status < 300) and response_type == "success"
	if not ok and not error:
		error = raw or _("HTTP {0}").format(status)
	return {"ok": ok, "http_status": status, "request_id": request_id,
		"response_type": response_type, "raw": raw, "error": None if ok else error}


def deliver_sms(message: str, mobile_no: str, dynamic_params=None) -> dict:
	"""Send `message` to one number; return {ok, http_status, request_id, raw, error}.
	`ok` is True only on a 2xx gateway response. The raw body is captured because
	MSG91 returns HTTP 200 + a request-id even for messages the operator later
	drops — the request-id is what you look up in the MSG91 dashboard."""
	result = {"ok": False, "http_status": None, "request_id": None, "raw": "", "error": None}
	try:
		numbers = validate_receiver_nos([mobile_no])
		captured = _send_and_capture(numbers[0], message, dynamic_params or [])
		result.update(captured)
	except Exception as e:
		result["error"] = str(e)
		frappe.log_error(frappe.get_traceback(), "deliver_sms failed")
	return result


def _send_and_capture(number, message, dynamic_params):
	from yrp.yrp.doctype.notification_template import notification_template as nt

	ss = frappe.get_doc("SMS Settings", "SMS Settings")
	if not ss.sms_gateway_url:
		frappe.throw(_("Please Update SMS Settings"))
	headers = nt.get_headers(ss)
	for d in dynamic_params:
		if d.get("header"):
			headers[d["parameter"]] = d["value"]
	use_json = headers.get("Content-Type") == "application/json"

	args = {ss.message_parameter: frappe.safe_decode(message)}
	for d in ss.get("parameters"):
		if not d.header:
			args[d.parameter] = d.value
	for d in dynamic_params:
		if not d.get("header"):
			args[d["parameter"]] = d["value"]
	args[ss.receiver_parameter] = number

	response = nt.send_request(ss.sms_gateway_url, args, headers, ss.use_post, use_json)
	raw = (response.text or "").strip()
	ok = 200 <= response.status_code < 300
	return {"ok": ok, "http_status": response.status_code, "raw": raw,
		"request_id": raw if ok else None, "error": None if ok else raw}
