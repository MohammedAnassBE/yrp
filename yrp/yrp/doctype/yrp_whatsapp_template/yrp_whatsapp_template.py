# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class YRPWhatsAppTemplate(Document):
	def validate(self):
		# A hand-created template always starts life as DRAFT. A sync-from-hub
		# write sets doc.flags.from_meta_sync and carries Meta's real status,
		# so it is exempt.
		if self.is_new() and not self.flags.from_meta_sync:
			self.status = "DRAFT"

	def is_applicable_for(self, doctype):
		"""Whether this template's user-configured Applicable DocTypes lists
		`doctype`. This is the template-centric replacement for the old
		per-doctype YRP WhatsApp Template Config row."""
		return any(
			row.reference_doctype == doctype for row in (self.applicable_doctypes or [])
		)

	def on_update(self):
		# Load-bearing guard: every hub-driven write sets flags.from_meta_sync
		# so the mirror is never pushed back to Meta. yrp does no create-on-Meta
		# in v1, so on_update is otherwise a no-op -- but the flag + guard are
		# kept for fidelity with the reference and the later authoring phase.
		if self.flags.from_meta_sync:
			return
