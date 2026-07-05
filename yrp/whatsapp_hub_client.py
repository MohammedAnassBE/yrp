# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import json

import frappe
import requests


def _get_settings():
	"""Load the hub connection Single; throw when the integration is off."""
	settings = frappe.get_single("YRP WhatsApp Hub Settings")
	if not settings.enabled:
		frappe.throw("WhatsApp Hub integration is not enabled")
	return settings


def _call_hub_api(method, data=None):
	"""Make an authenticated API call to the hub site.

	Builds ``{hub_url}/api/method/frappe_whatsapp_integration.frappe_whatsapp_hub.api.{method}``,
	POSTs the token header via plain ``requests.post``, ``raise_for_status()``,
	and unwraps Frappe's ``{"message": …}`` envelope.
	"""
	settings = _get_settings()
	url = f"{settings.get_hub_url()}/api/method/frappe_whatsapp_integration.frappe_whatsapp_hub.api.{method}"
	headers = settings.get_hub_auth_headers()
	response = requests.post(url, headers=headers, json=data or {}, timeout=30)
	response.raise_for_status()
	result = response.json()
	return result.get("message", result)


def _get_account_name(account=None):
	"""Resolve the hub account name from a WhatsApp Account doc, a bare
	string, or fall back to the configured default."""
	settings = _get_settings()
	if account and hasattr(account, "account_name"):
		return settings.get_account_name(account.account_name)
	if account and isinstance(account, str):
		return settings.get_account_name(account)
	return settings.get_default_account_name()


def hub_enabled():
	"""Check if hub integration is enabled. Never raises."""
	try:
		settings = frappe.get_single("YRP WhatsApp Hub Settings")
		return bool(settings.enabled)
	except Exception:
		return False


# --- Message sending ---

def send_template_message(whatsapp_account, to_number, template_name, language_code="en", components=None):
	"""Send an approved template. Inspects ``components``: a media header
	(document / image / video) routes to ``send.send_template_with_document``
	with a lowercase ``header_format`` and ``document_id`` preferred over a
	url; otherwise ``send.send_template``. Returns ``(success, result|error)``.
	"""
	account_name = _get_account_name(whatsapp_account)

	body_variables = []
	header_variables = []
	document_url = None
	document_id = None
	document_filename = None
	image_link = None
	image_id = None
	video_link = None
	video_id = None

	if components:
		for comp in components:
			ctype = comp.get("type")
			params = comp.get("parameters", []) or []
			if ctype == "body":
				body_variables = [p.get("text", "") for p in params if p.get("type") == "text"]
			elif ctype == "header":
				for p in params:
					ptype = p.get("type")
					if ptype == "text":
						header_variables.append(p.get("text", ""))
					elif ptype == "document":
						doc = p.get("document", {}) or {}
						document_url = doc.get("link") or doc.get("url")
						document_id = doc.get("id")
						document_filename = doc.get("filename")
					elif ptype == "image":
						img = p.get("image", {}) or {}
						image_link = img.get("link") or img.get("url")
						image_id = img.get("id")
					elif ptype == "video":
						vid = p.get("video", {}) or {}
						video_link = vid.get("link") or vid.get("url")
						video_id = vid.get("id")

	try:
		if document_url or document_id:
			result = _call_hub_api("send.send_template_with_document", {
				"to_number": to_number,
				"template_name": template_name,
				"language_code": language_code,
				"document_url": document_url,
				"document_id": document_id,
				"document_filename": document_filename,
				"header_format": "document",
				"body_variables": json.dumps(body_variables) if body_variables else None,
				"account_name": account_name,
			})
		elif image_link or image_id:
			result = _call_hub_api("send.send_template_with_document", {
				"to_number": to_number,
				"template_name": template_name,
				"language_code": language_code,
				"document_url": image_link,
				"document_id": image_id,
				"header_format": "image",
				"body_variables": json.dumps(body_variables) if body_variables else None,
				"account_name": account_name,
			})
		elif video_link or video_id:
			result = _call_hub_api("send.send_template_with_document", {
				"to_number": to_number,
				"template_name": template_name,
				"language_code": language_code,
				"document_url": video_link,
				"document_id": video_id,
				"header_format": "video",
				"body_variables": json.dumps(body_variables) if body_variables else None,
				"account_name": account_name,
			})
		else:
			result = _call_hub_api("send.send_template", {
				"to_number": to_number,
				"template_name": template_name,
				"language_code": language_code,
				"body_variables": json.dumps(body_variables) if body_variables else None,
				"header_variables": json.dumps(header_variables) if header_variables else None,
				"account_name": account_name,
			})
		if result.get("success"):
			return True, result
		# Meta failure: return the FULL hub dict (carries error/meta_error/
		# status_code) so the caller can log meta_error + http_status.
		return False, result
	except Exception as e:
		# transport exception: a bare string is all we have
		return False, str(e)


def send_media_message(whatsapp_account, to_number, media_id, media_type, caption=None):
	"""Send a standalone (non-template) media message by media_id."""
	account_name = _get_account_name(whatsapp_account)
	try:
		result = _call_hub_api("send.send_media", {
			"to_number": to_number,
			"media_type": media_type,
			"media_id": media_id,
			"caption": caption,
			"account_name": account_name,
		})
		if result.get("success"):
			return True, result
		# Meta failure: return the FULL hub dict (carries error/meta_error/
		# status_code) so the caller can log meta_error + http_status.
		return False, result
	except Exception as e:
		# transport exception: a bare string is all we have
		return False, str(e)


# --- Media operations ---

def upload_media(whatsapp_account, file_content_b64, content_type, filename):
	"""Upload already-base64-encoded bytes through the hub to Meta.

	Returns the hub's raw result dict ``{"success": True, "media_id": …}`` or
	``{"success": False, "error": …}``. Transport errors from ``_call_hub_api``
	propagate to the caller (``yrp/whatsapp.py::_upload_header`` catches them).
	"""
	account_name = _get_account_name(whatsapp_account)
	return _call_hub_api("media.upload_media", {
		"account_name": account_name,
		"file_content_b64": file_content_b64,
		"content_type": content_type,
		"filename": filename,
	})


# --- Template sync ---

def sync_templates_from_meta(account_name):
	"""Ask the hub to refresh templates from Meta for ``account_name`` and
	return ``{"synced": …, "data": [ … Meta-shape templates … ]}``."""
	return _call_hub_api("templates.sync_templates_from_meta", {
		"account_name": account_name,
	})
