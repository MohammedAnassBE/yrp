# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document

# A template placeholder: {field}, {doc.name}, { DocName } — word chars + dots.
PLACEHOLDER_RE = re.compile(r"\{\s*([\w.]+)\s*\}")


class YRPSMSSettings(Document):
	def get_template_config(self, reference_doctype, template_name=None):
		"""Return the configured row for `reference_doctype` (optionally the one
		named `template_name`), or None. A DocType may have several templates."""
		rows = [r for r in (self.templates or []) if r.reference_doctype == reference_doctype]
		if template_name:
			rows = [r for r in rows if r.template_name == template_name]
		return rows[0] if rows else None

	def get_templates_for_doctype(self, reference_doctype):
		"""Every configured template row for a DocType."""
		return [r for r in (self.templates or []) if r.reference_doctype == reference_doctype]


def get_sms_config(reference_doctype, template_name=None):
	"""Resolve the gateway + template for a doctype (and optional template name).

	Returns {gateway_url, template_id, template_name, template_body, authkey,
	country_code}. Raises if SMS is disabled or no matching template row exists —
	deliver_flow_sms turns that into a Failed log rather than a silent no-op."""
	settings = frappe.get_cached_doc("YRP SMS Settings")
	if not settings.enabled:
		frappe.throw(_("YRP SMS Settings is disabled"))
	row = settings.get_template_config(reference_doctype, template_name)
	if not row:
		frappe.throw(_("No SMS template configured for {0} in YRP SMS Settings").format(reference_doctype))
	if not settings.sms_gateway_url:
		frappe.throw(_("SMS Gateway URL is not set in YRP SMS Settings"))
	return {
		"gateway_url": settings.sms_gateway_url,
		"template_id": row.template_id,
		"template_name": row.template_name,
		"template_body": row.template_body,
		"authkey": row.get_password("authkey"),
		"country_code": settings.country_code,
	}


def parse_template_variables(template_body):
	"""Distinct {placeholder} tokens in a template body, in first-seen order."""
	if not template_body:
		return []
	seen, ordered = set(), []
	for match in PLACEHOLDER_RE.finditer(template_body):
		token = match.group(1)
		if token not in seen:
			seen.add(token)
			ordered.append(token)
	return ordered
