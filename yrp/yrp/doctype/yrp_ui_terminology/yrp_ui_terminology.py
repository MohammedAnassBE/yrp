# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document


TERM_KEY_RE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*")


class YRPUITerminology(Document):
	def validate(self):
		seen = set()
		for row in self.terms or []:
			key = (row.term_key or "").strip()
			if not TERM_KEY_RE.fullmatch(key):
				frappe.throw(
					_("Row {0}: Term Key must use lowercase semantic segments separated by dots.").format(
						row.idx
					)
				)
			if key in seen:
				frappe.throw(_("Row {0}: Term Key {1} is duplicated.").format(row.idx, frappe.bold(key)))
				seen.add(key)
				row.term_key = key
