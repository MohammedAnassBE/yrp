"""Visit-derived demand, validated item quantities and summary claim protection."""
import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from yrp.yrp_retail.logic import finite_number, require, validate_assignment, validate_order_items


def on_doctype_update():
	frappe.db.add_unique("YRP Retail Order", ["visit"], constraint_name="unique_retail_order_visit")


class YRPRetailOrder(Document):
	def validate(self):
		self._ensure_unclaimed()
		old = self.get_doc_before_save()
		if old:
			require(not old.summary, "An order included in a summary cannot be edited.")
			require((self.summary or "") == (old.summary or ""), "Summary links are managed by Retail Order Summaries.")
		else:
			require(not self.summary, "Summary links are managed by Retail Order Summaries.")
		require(self.visit, "Visit is required.")
		# Serialize against visit edits and concurrent order creation.
		frappe.db.sql("select name from `tabYRP Visit` where name=%s for update", self.visit)
		visit = frappe.get_doc("YRP Visit", self.visit, for_update=True)
		self.order_date = getdate(visit.visit_datetime)
		for field, source in (("sales_person", "sales_person"), ("customer", "customer"), ("retailer", "retailer"), ("order_type", "visit_type")):
			value = visit.get(source)
			require(not self.get(field) or self.get(field) == value, "Order header conflicts with its Visit.")
			self.set(field, value)
		validate_assignment(self.sales_person, self.customer, self.retailer)
		validate_order_items(self.items)
		self.total_qty = finite_number(sum(row.qty for row in self.items), "Total quantity")

	def on_update(self):
		self._refresh_visit(self.visit)
		old = self.get_doc_before_save()
		if old and old.visit != self.visit:
			self._refresh_visit(old.visit)

	def on_trash(self):
		self._ensure_unclaimed()
		require(not self.summary, "An order included in a summary cannot be deleted.")
		self._refresh_visit(self.visit, exclude=self.name)

	def before_cancel(self):
		# Frappe does not run validate() when cancelling. Draft summaries also
		# claim demand, so native submitted-document backlink checks are not enough.
		self._ensure_unclaimed()

	def before_discard(self):
		self._ensure_unclaimed()

	def _ensure_unclaimed(self):
		if not self.is_new():
			rows = frappe.db.sql("select summary from `tabYRP Retail Order` where name=%s for update", self.name)
			require(not rows or not rows[0][0], "An order included in a summary cannot be edited, cancelled or deleted.")

	def _refresh_visit(self, visit, exclude=None):
		# A cancelled order remains the visit's historical order. Retain the
		# one-order-per-visit invariant and avoid offering a duplicate creation.
		filters = {"visit": visit}
		if exclude:
			filters["name"] = ["!=", exclude]
		frappe.db.set_value("YRP Visit", visit, "has_order", int(bool(frappe.db.exists("YRP Retail Order", filters))))
