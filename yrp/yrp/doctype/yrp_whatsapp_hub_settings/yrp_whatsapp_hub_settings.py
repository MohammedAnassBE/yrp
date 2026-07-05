# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document

# Positional WhatsApp template variable token: {{1}}, {{ 2 }} — Meta's numbered placeholders.
WA_VAR_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")


class YRPWhatsAppHubSettings(Document):
	def get_hub_auth_headers(self):
		api_key = self.get_password("api_key", raise_exception=False)
		api_secret = self.get_password("api_secret", raise_exception=False)
		return {
			"Authorization": f"token {api_key}:{api_secret}",
			"Content-Type": "application/json",
		}

	def get_hub_url(self):
		return (self.hub_url or "").rstrip("/")

	def get_default_account_name(self):
		for acc in self.accounts:
			if acc.is_default:
				return acc.account_name
		if self.accounts:
			return self.accounts[0].account_name
		frappe.throw("No WhatsApp Hub accounts configured")

	def get_account_name(self, account_name=None):
		if account_name:
			for acc in self.accounts:
				if acc.account_name == account_name:
					return acc.account_name
			frappe.throw(f"Account '{account_name}' not found in hub settings")
		return self.get_default_account_name()

	def get_enabled_doctypes(self):
		"""Every reference_doctype enabled in the allowlist, in row order."""
		return [
			row.reference_doctype
			for row in (self.enabled_doctypes or [])
			if row.enabled and row.reference_doctype
		]

	def is_doctype_enabled(self, doctype):
		"""Whether `doctype` has an enabled row in the allowlist."""
		return doctype in self.get_enabled_doctypes()

	def get_supplier_key(self, doctype):
		"""The allowlist row's supplier_key for `doctype`, or the "supplier"
		default when the row has none configured (or the doctype isn't listed)."""
		for row in (self.enabled_doctypes or []):
			if row.reference_doctype == doctype:
				return row.supplier_key or "supplier"
		return "supplier"

	def on_update(self):
		self._sync_local_whatsapp_accounts()

	def _sync_local_whatsapp_accounts(self):
		for row in (self.get("accounts") or []):
			name = (row.account_name or "").strip()
			if not name:
				continue
			is_default = 1 if row.get("is_default") else 0
			if frappe.db.exists("YRP WhatsApp Account", name):
				frappe.db.set_value("YRP WhatsApp Account", name, {
					"is_default": is_default,
				})
			else:
				frappe.get_doc({
					"doctype": "YRP WhatsApp Account",
					"account_name": name,
					"is_default": is_default,
				}).insert(ignore_permissions=True, ignore_mandatory=True)


def parse_whatsapp_variables(text):
	"""Distinct positional {{n}} tokens in a template body, as ints in first-seen order."""
	if not text:
		return []
	seen, ordered = set(), []
	for match in WA_VAR_RE.finditer(text):
		n = int(match.group(1))
		if n not in seen:
			seen.add(n)
			ordered.append(n)
	return ordered
