"""Validated visit identity and location; ordered visits retain their original context."""
import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

from yrp.yrp_retail.logic import require, validate_assignment, validate_location


class YRPVisit(Document):
	def validate(self):
		require(self.visit_type in ("Primary", "Secondary"), "Invalid visit type.")
		if self.visit_type == "Primary":
			require(not self.retailer, "Primary visits cannot have a Retailer.")
		else:
			require(self.retailer, "Secondary visits require a Retailer.")
			customer = frappe.db.get_value("YRP Retailer", self.retailer, "customer")
			require(customer, "Retailer must have a Customer.")
			require(not self.customer or self.customer == customer, "Customer conflicts with the Retailer's Customer.")
			self.customer = customer
		validate_assignment(self.sales_person, self.customer, self.retailer)
		validate_location(self.location)
		if not self.is_new():
			frappe.db.sql("select name from `tabYRP Visit` where name=%s for update", self.name)
		old = self.get_doc_before_save()
		# A current read must see orders committed after this transaction began.
		active_order = frappe.db.sql("select name from `tabYRP Retail Order` where visit=%s for update", self.name) if not self.is_new() else None
		if old and active_order:
			for field in ("sales_person", "customer", "retailer", "visit_type", "visit_datetime"):
				same = get_datetime(self.get(field)) == get_datetime(old.get(field)) if field == "visit_datetime" else (self.get(field) or "") == (old.get(field) or "")
				require(same, "A visit with an order cannot change its assignment, type or time.")
		# This indicator is derived, including cancelled order history. Ignore
		# incoming values so Desk, imports and APIs cannot set it manually.
		self.has_order = int(bool(active_order))
