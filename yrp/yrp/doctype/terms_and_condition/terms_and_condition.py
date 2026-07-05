# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

PO = "PO"
WO = "WO"


class TermsandCondition(Document):
	DEFAULT_FLAGS = ("is_default_po_term", "is_default_wo_term", "is_default_company")

	def validate(self):
		# "Default things are always single" — at most one Terms and Condition may
		# hold each default flag, so setting one clears it on every other record.
		for flag in self.DEFAULT_FLAGS:
			self._enforce_single_default(flag)

	def _enforce_single_default(self, flag):
		if not self.get(flag):
			return
		for name in frappe.get_all(
			"Terms and Condition",
			filters={flag: 1, "name": ["!=", self.name or ""]},
			pluck="name",
		):
			frappe.db.set_value("Terms and Condition", name, flag, 0)


def get_default_terms(transaction_type, supplier=None):
	"""Resolve the default Terms and Condition for a transaction, in priority order:

	1. the supplier's mapped term for this transaction type,
	2. the term flagged default for this transaction type,
	3. the term flagged default for the company,
	4. None.

	`transaction_type` is "PO" (Purchase Order) or "WO" (Work Order).
	"""
	if transaction_type not in (PO, WO):
		return None

	supplier_field = "po_terms_and_condition" if transaction_type == PO else "wo_terms_and_condition"
	default_flag = "is_default_po_term" if transaction_type == PO else "is_default_wo_term"

	if supplier:
		mapped = frappe.db.get_value("Supplier", supplier, supplier_field)
		if mapped:
			return mapped

	txn_default = frappe.db.get_value("Terms and Condition", {default_flag: 1}, "name")
	if txn_default:
		return txn_default

	return frappe.db.get_value("Terms and Condition", {"is_default_company": 1}, "name")
