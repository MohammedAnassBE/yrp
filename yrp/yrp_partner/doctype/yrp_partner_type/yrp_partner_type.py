"""Configuration selecting the source DocType and generated-email policy."""

import frappe
from frappe import _
from frappe.model.document import Document


class YRPPartnerType(Document):
	def validate(self):
		if self.generate_email_when_missing:
			from yrp.yrp_partner.users import validate_email_format

			validate_email_format(self.generated_email_format)
		meta = frappe.get_meta(self.reference_doctype)
		if meta.issingle or meta.istable or meta.is_virtual or meta.module == "YRP Partner":
			frappe.throw(_("Select a regular document type outside YRP Partner."))
		previous = self.get_doc_before_save()
		if previous and previous.reference_doctype != self.reference_doctype:
			if frappe.db.exists("YRP Partner", {"partner_type": self.name}):
				frappe.throw(_("Reference DocType cannot change after partners have been generated. Create another Partner Type."))

	def on_update(self):
		from yrp.yrp_partner.setup import ensure_partner_read_permission

		ensure_partner_read_permission(self.reference_doctype)
		from yrp.yrp_partner.backfill import sync_partner_type

		sync_partner_type(self)
