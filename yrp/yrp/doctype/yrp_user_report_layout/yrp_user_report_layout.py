import hashlib

import frappe
from frappe.model.document import Document


def on_doctype_update():
	frappe.db.add_unique(
		"YRP User Report Layout",
		["user", "report_key"],
		constraint_name="unique_yrp_user_report_layout",
	)


class YRPUserReportLayout(Document):
	def autoname(self):
		identity = f"{self.user}\0{self.report_key}".encode()
		self.name = hashlib.sha256(identity).hexdigest()
