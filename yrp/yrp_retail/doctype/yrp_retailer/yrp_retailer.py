"""Retail outlet identified by its ID and linked to its Customer and Sales Person."""
import frappe
from frappe.model.document import Document
from yrp.yrp_retail.logic import require, validate_assignment


class YRPRetailer(Document):
	def before_validate(self):
		"""Customer is authoritative, including API/import writes and cleared links."""
		require(self.customer, "Customer is required for a Retailer.")
		if self.get("primary_address"):
			address = frappe.get_doc("Address", self.primary_address)
			address.check_permission("read")
		self.sales_partner = frappe.db.get_value("Customer", self.customer, "default_sales_partner", for_update=True) or None

	def validate(self):
		require(self.get("sales_person"), "Sales Person is required for a Retailer.")
		old = self.get_doc_before_save()
		# Registration provenance may outlive its assignment. An assigned colleague
		# can still edit shop details without rewriting the original Sales Person.
		if not old or any(self.get(field) != old.get(field) for field in ("customer", "sales_person")):
			validate_assignment(self.sales_person, self.customer)
		if old and any(self.get(field) != old.get(field) for field in ("customer", "sales_person")):
			if frappe.db.table_exists("YRP Visit"):
				require(not frappe.db.exists("YRP Visit", {"retailer": self.name}), "A retailer with visits cannot change its Customer or Sales Person.")
