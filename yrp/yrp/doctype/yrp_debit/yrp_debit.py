import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from yrp import attribute_links as attribute_db


class YRPDebit(Document):
	def validate(self):
		if self.is_new() and not self.flags.ignore_permissions:
			if not _user_has_settings_role("debit_request_role"):
				frappe.throw(_("You do not have permission to request a Debit."))
		if not self.inspection and not self.debit_no:
			frappe.throw(_("Debit No is required."))
		if self.inspection and not self.debit_document:
			frappe.throw(_("Debit Document is required."))

	def before_submit(self):
		if _user_has_settings_role("debit_approval_role"):
			self.status = "Approved"
			self.approved_by = frappe.session.user
		else:
			self.status = "Debit Requested"


@frappe.whitelist()
def get_work_order_defaults(work_order):
	# Debit is a standalone voucher with no item child table, so the only
	# value to prefill from the source Work Order is the work_order link
	# itself. Mirrors delivery_challan.get_work_order_defaults' shape.
	return {"work_order": work_order}


@frappe.whitelist()
def approve_debit(name):
	if not _user_has_settings_role("debit_approval_role"):
		frappe.throw(_("You do not have permission to approve debits."))

	doc = frappe.get_doc("YRP Debit", name)
	if doc.docstatus != 1:
		frappe.throw(_("Debit must be submitted before approval."))
	if doc.status != "Debit Requested":
		return doc.status
	doc.status = "Approved"
	doc.approved_by = frappe.session.user
	doc.save(ignore_permissions=True)
	return doc.status


@frappe.whitelist()
def has_debit_request_permission() -> bool:
	return bool(
		_user_has_settings_role("debit_request_role")
		and frappe.has_permission("YRP Debit", "create")
		and frappe.has_permission("YRP Debit", "submit")
	)


@frappe.whitelist()
def create_debit(work_order, debit_no=None, debit_value=None, reason=None, on_close=0, debit_document=None):
	if int(on_close or 0):
		if not _user_has_settings_role("work_order_closing_approver_role"):
			frappe.throw(_("You do not have permission to request a Debit."))
	else:
		if not has_debit_request_permission():
			frappe.throw(_("You do not have permission to request a Debit."), frappe.PermissionError)
		work_order_state = frappe.db.get_value(
			"YRP Work Order", work_order, ["docstatus", "open_status"], as_dict=True
		)
		if not work_order_state or work_order_state.docstatus != 1 or work_order_state.open_status != "Open":
			frappe.throw(_("Debit can only be created for an open submitted Work Order."))

	if flt(debit_value) <= 0:
		frappe.throw(_("Debit Value must be greater than zero."))
	if not (reason or "").strip():
		frappe.throw(_("Reason is required."))

	doc = frappe.get_doc(
		{
			"doctype": "YRP Debit",
			"work_order": work_order,
			"debit_type": "Permanent",
			"debit_no": debit_no,
			"debit_value": debit_value,
			"reason": reason,
			"debit_document": debit_document,
			"on_close": on_close,
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc.as_dict()


def _user_has_settings_role(field):
	role = attribute_db.get_single_value("YRP Settings", field)
	if not role:
		return False
	return role in frappe.get_roles(frappe.session.user)


Debit = YRPDebit
