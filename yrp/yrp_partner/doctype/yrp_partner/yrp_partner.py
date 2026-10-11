"""Generated partner identity and its explicit user memberships."""

import frappe
from frappe import _
from frappe.model.document import Document


class YRPPartner(Document):
	def before_insert(self):
		from yrp.yrp_partner.sync import _GENERATION_TOKEN

		if self.flags.generation_token is not _GENERATION_TOKEN:
			frappe.throw(_("Partners are generated automatically from the configured source records."), frappe.PermissionError)

	def validate(self):
		expected = frappe.db.get_value("YRP Partner Type", self.partner_type, "reference_doctype")
		if expected != self.reference_doctype:
			frappe.throw(_("Reference DocType must match the Partner Type."))
		previous = self.get_doc_before_save()
		if previous and any(previous.get(f) != self.get(f) for f in ("partner_type", "reference_doctype", "reference_name")):
			frappe.throw(_("The generated partner reference cannot be changed."))
		users = [row.user for row in self.users]
		if len(users) != len(set(users)):
			frappe.throw(_("A user can only be added once to this partner."))

	def on_update(self):
		from yrp.yrp_partner.member_roles import get_member_roles, sync_member_roles

		previous = self.get_doc_before_save()
		before = {row.user for row in previous.users} if previous else set()
		changed = before ^ {row.user for row in self.users}
		sync_member_roles(changed, get_member_roles({self.reference_doctype}))

	def after_delete(self):
		from yrp.yrp_partner.member_roles import get_member_roles, sync_member_roles

		sync_member_roles({row.user for row in self.users}, get_member_roles({self.reference_doctype}))


def on_doctype_update():
	frappe.db.add_unique("YRP Partner", ["partner_type", "reference_name"])
