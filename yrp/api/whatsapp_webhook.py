# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import hashlib
import json

import frappe
from frappe import _


def _assert_webhook_user():
	"""Pin the caller to the dedicated hub user from ``YRP WhatsApp Hub Settings``.

	``allow_guest=False`` already rejects an unauthenticated Guest; this closes
	the remaining gap where *any other* authenticated user could forge a delivery
	status (a spoofed ``failed`` -> double-send) or inject audit rows. Raises
	``frappe.PermissionError`` on mismatch.
	"""
	webhook_user = frappe.db.get_single_value(
		"YRP WhatsApp Hub Settings", "webhook_user"
	)
	# Opt-in pin: enforce ONLY when a webhook_user is configured. When it is unset,
	# fall back to the base guarantee (allow_guest=False + Frappe token auth) so an
	# unconfigured spoke degrades to "any authenticated caller" (the reference
	# behaviour) instead of hard-rejecting every hub call with 403. Set webhook_user
	# to the dedicated hub caller to turn the extra hardening on.
	if webhook_user and frappe.session.user != webhook_user:
		raise frappe.PermissionError(
			_("WhatsApp webhook caller is not the configured webhook_user")
		)


def _raw_request_body():
	"""Raw POST body as text; tolerant of a missing or unreadable body."""
	req = getattr(frappe.local, "request", None)
	if req is None:
		return ""
	try:
		return req.get_data(as_text=True) or ""
	except Exception:
		return ""


def _parse_payload(raw_text):
	"""Parse the raw body into a dict; returns ``{}`` for a garbage body."""
	if not raw_text:
		return {}
	try:
		data = json.loads(raw_text)
	except Exception:
		return {}
	return data if isinstance(data, dict) else {}


def _is_replay(raw_text):
	"""Best-effort replay dedupe on the payload hash.

	Fails OPEN (returns ``False``) on any cache error so a real webhook is never
	silently dropped. Mirrors the hub's own dedupe; the downstream status
	application is monotonic/idempotent anyway, so this only skips redundant work.
	"""
	if not raw_text:
		return False
	key = "yrp:wa:webhook:" + hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
	try:
		cache = frappe.cache()
		if cache.get_value(key):
			return True
		cache.set_value(key, 1, expires_in_sec=600)
	except Exception:
		return False
	return False


@frappe.whitelist(allow_guest=False)
def receive(**kwargs):
	"""Ingest a hub-forwarded WhatsApp webhook (delivery ``statuses[]`` +
	``template_status_update``). Status-only spoke: inbound customer
	``messages[]`` are counted by the processor, never stored.

	Invariants:
	  1. assert the caller is the configured ``webhook_user`` (else PermissionError);
	  2. write the raw body to a ``YRP WhatsApp Webhook Log`` FIRST — the audit row
	     must survive a later processing crash;
	  3. delegate to ``whatsapp_inbound.process_payload`` (guarded; it owns its own
	     savepoints and never raises out), unless the body is a replay;
	  4. ALWAYS return ``{"ok": True}`` so the hub does not retry-storm — real
	     failures land in the Error Log.
	"""
	_assert_webhook_user()

	# Capture the verbatim body BEFORE parsing so a malformed body still lands
	# in `raw`; `payload` holds the parsed-then-reserialized JSON (or None).
	raw_text = _raw_request_body()
	data = _parse_payload(raw_text)

	# (2) Persist the raw payload FIRST. Never lose an audit row to a crash.
	log = None
	try:
		log = frappe.get_doc({
			"doctype": "YRP WhatsApp Webhook Log",
			"payload": frappe.as_json(data) if data else None,
			"raw": raw_text,
			"processed": 0,
		})
		log.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title="YRP WhatsApp webhook: log insert failed",
			message=frappe.get_traceback(),
		)

	# (3) Skip re-processing an identical body (already logged above).
	if _is_replay(raw_text):
		return {"ok": True}

	try:
		from yrp.whatsapp_inbound import process_payload

		process_payload(data, webhook_log=log)
	except Exception:
		frappe.log_error(
			title="YRP WhatsApp webhook: processing failed",
			message=frappe.get_traceback(),
		)

	# (4) Always OK.
	return {"ok": True}


@frappe.whitelist(allow_guest=False)
def receive_push(**kwargs):
	"""Hub-initiated template push. Body: ``{"template": {…Meta shape…}}``.

	Same ``webhook_user`` pin as :func:`receive`. Upserts the Meta-shape template
	into the local ``YRP WhatsApp Template`` mirror and returns
	``{"upserted", "name"}``.
	"""
	_assert_webhook_user()

	data = _parse_payload(_raw_request_body())
	template = data.get("template") or kwargs.get("template")
	if not template:
		frappe.throw(_("Missing 'template' in payload"))

	from yrp.whatsapp_hub_client import _get_account_name
	from yrp.whatsapp_templates import _upsert_local_template

	whatsapp_account = _get_account_name(None)
	try:
		name = _upsert_local_template(template, whatsapp_account)
		return {"upserted": 1, "name": name}
	except Exception as e:
		frappe.log_error(
			title="YRP WhatsApp template receive_push failed",
			message=frappe.get_traceback(),
		)
		return {"upserted": 0, "error": str(e)}
