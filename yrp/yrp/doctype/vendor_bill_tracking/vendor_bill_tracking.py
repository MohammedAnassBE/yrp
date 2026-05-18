import frappe
import frappe.utils
from frappe.model.document import Document

from yrp.yrp.doctype.department.department import get_user_departments


class VendorBillTracking(Document):
	def before_insert(self):
		self.check_for_unique_condition()
		if self.amended_from:
			self.set_amended_log()

	def before_submit(self):
		if not self.amended_from:
			self.set_first_history()

	def before_cancel(self):
		self.set_cancelled_log()

	def check_for_unique_condition(self):
		"""Reject duplicate (supplier, bill_no) within the configured fiscal year.

		Toggled via YRP Settings.unique_vendor_bill_per_year (0 disables).
		"""
		flag = frappe.db.get_single_value(
			"YRP Settings", "unique_vendor_bill_per_year"
		)
		if not flag:
			return
		start_date = frappe.db.get_single_value("YRP Settings", "fiscal_year_start_date")
		end_date = frappe.db.get_single_value("YRP Settings", "fiscal_year_end_date")
		if not start_date or not end_date:
			return
		exist_bills = frappe.get_all(
			"Vendor Bill Tracking",
			filters=[
				["bill_date", "between", [start_date, end_date]],
				["supplier", "=", self.supplier],
				["bill_no", "=", self.bill_no],
				["docstatus", "!=", 2],
			],
		)
		if exist_bills:
			frappe.throw(
				"The following bills already exist:<br>"
				+ "<br>".join(
					f"<a href='/app/vendor-bill-tracking/{b['name']}' target='_blank'>{b['name']}</a>"
					for b in exist_bills
				)
			)

	def set_amended_log(self):
		self.append("vendor_bill_tracking_history", {
			"assigned_by": frappe.session.user,
			"assigned_on": frappe.utils.now_datetime(),
			"action": "Amend",
		})
		self.set("form_status", "Amended")

	def set_cancelled_log(self):
		self.append("vendor_bill_tracking_history", {
			"assigned_by": frappe.session.user,
			"assigned_on": frappe.utils.now_datetime(),
			"action": "Cancel",
		})
		self.set("form_status", "Cancelled")

	def set_first_history(self):
		if not self.vendor_bill_tracking_history:
			self.append("vendor_bill_tracking_history", {
				"assigned_by": frappe.session.user,
				"assigned_on": frappe.utils.now_datetime(),
				"action": "Open",
			})
			self.set("form_status", "Open")

	def close_vendor_bill(self, purchase_invoice, remarks=None):
		if self.docstatus != 1:
			frappe.throw("Vendor Bill must be submitted before it can be closed.")
		if self.form_status == "Closed":
			frappe.throw("Vendor Bill already closed.")
		self.append("vendor_bill_tracking_history", {
			"assigned_by": frappe.session.user,
			"assigned_on": frappe.utils.now_datetime(),
			"remarks": remarks,
			"action": "Close",
		})
		self.set("form_status", "Closed")
		self.set("purchase_invoice", purchase_invoice)

	def reopen_vendor_bill(self, remarks=None):
		self.append("vendor_bill_tracking_history", {
			"assigned_by": frappe.session.user,
			"assigned_on": frappe.utils.now_datetime(),
			"remarks": remarks,
			"action": "Reopen",
		})
		self.set("form_status", "Reopen")
		self.set("purchase_invoice", None)

	def assign_bill_to_department(self, department, remarks=None):
		self.append("vendor_bill_tracking_history", {
			"assigned_to": department,
			"assigned_on": frappe.utils.now_datetime(),
			"assigned_by": frappe.session.user,
			"remarks": remarks,
			"action": "Assign",
		})
		self.set("form_status", "Assigned")
		self.set("assigned_to", department)


# ----------------------------------------------------------------------
# Whitelist API
# ----------------------------------------------------------------------
@frappe.whitelist()
def assign_vendor_bill(name, assigned_to, remarks=None):
	from yrp.yrp.doctype.supplier.supplier import update_supplier_department_on_vbt

	doc = frappe.get_doc("Vendor Bill Tracking", name)
	if doc.docstatus != 1 or doc.form_status not in ("Reopen", "Open", "Assigned", "Amended"):
		frappe.throw(f"Cannot assign Vendor Bill {doc.name} (current status: {doc.form_status}).")
	doc.assign_bill_to_department(assigned_to, remarks)
	update_supplier_department_on_vbt(doc.supplier, assigned_to)
	doc.save(ignore_permissions=True)


@frappe.whitelist()
def close_vendor_bill(name, purchase_invoice, remarks=None):
	doc = frappe.get_doc("Vendor Bill Tracking", name)
	doc.close_vendor_bill(purchase_invoice, remarks)
	doc.save(ignore_permissions=True)


@frappe.whitelist()
def reopen_vendor_bill(name, remarks=None):
	doc = frappe.get_doc("Vendor Bill Tracking", name)
	doc.reopen_vendor_bill(remarks)
	doc.save(ignore_permissions=True)


@frappe.whitelist()
def revert_purchase_invoice_link(name, pi_name, origin=None):
	"""Called from Purchase Invoice before_cancel / on_trash. Clears the PI link
	on the VBT (only when the link still points at the *originating* PI), appends
	a Reopen history row, and flips form_status from Closed → Reopen.

	If the VBT was re-pointed to a different PI in the meantime, the revert is a
	no-op (logged). This prevents a stale PI cancellation from clobbering a
	freshly-linked replacement.
	"""
	doc = frappe.get_doc("Vendor Bill Tracking", name)
	current = doc.get("purchase_invoice")
	if current != pi_name:
		frappe.log_error(
			title="VBT revert skipped — PI mismatch",
			message=(
				f"VBT: {name}\n"
				f"Originating PI: {pi_name}\n"
				f"Current PI on VBT: {current}\n"
				f"Origin: {origin}"
			),
		)
		return
	if not current and doc.form_status != "Closed":
		# Link already cleared by an earlier hook (e.g. cancel before trash).
		return
	doc.set("purchase_invoice", None)
	doc.append("vendor_bill_tracking_history", {
		"assigned_to": doc.assigned_to,
		"assigned_on": frappe.utils.now_datetime(),
		"assigned_by": frappe.session.user,
		"remarks": f"Auto-reverted: purchase_invoice={pi_name} ({origin or 'cancelled/deleted'})",
		"action": "Reopen",
	})
	if doc.form_status == "Closed":
		doc.set("form_status", "Reopen")
	doc.save(ignore_permissions=True)


@frappe.whitelist()
def cancel_vendor_bill(name, cancel_reason):
	doc = frappe.get_doc("Vendor Bill Tracking", name)
	doc.cancel_reason = cancel_reason
	doc.flags.ignore_permissions = True
	doc.cancel()


@frappe.whitelist()
def make_bill_received_acknowledgement(doc_name):
	departments = get_user_departments()
	vbt_doc = frappe.get_doc("Vendor Bill Tracking", doc_name)
	if vbt_doc.assigned_to not in departments:
		frappe.throw("This bill is not assigned to your department.")
	last_doc = None
	for row in vbt_doc.vendor_bill_tracking_history:
		if row.get("assigned_to") == vbt_doc.get("assigned_to"):
			last_doc = row
	if not last_doc:
		frappe.throw("Invalid operation: no matching assignment row found.")
	last_doc.received = 1
	vbt_doc.save(ignore_permissions=True)


@frappe.whitelist()
def bulk_assign_bills(assign_to, selected_docs, remarks=None):
	if isinstance(selected_docs, str):
		selected_docs = frappe.json.loads(selected_docs)
	for entry in selected_docs:
		try:
			assign_vendor_bill(entry["name"], assign_to, remarks)
		except Exception:
			# Skip failures and continue (mirror production_api). A failed row
			# stays in its current state; the user will see error logs.
			pass


@frappe.whitelist()
def check_for_can_show_receive_btn(name):
	department = frappe.get_value("Vendor Bill Tracking", name, "assigned_to")
	if not department:
		return False
	return bool(get_user_departments(department))
