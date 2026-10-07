import frappe

ROLE_DEFAULTS = {
	"purchase_order_manager_role": "Purchase Manager",
	"bill_tracking_cancel_request_role": "HR User",
	"bill_tracking_cancel_approver_role": "HR Manager",
}
BILL_TRACKING_INVOICE_ROLES = ("Accounts Manager", "Accounts User")


def execute():
	"""Seed role settings with the roles that used to be hard-coded; never overwrite."""
	settings = frappe.get_single("YRP Settings")
	changed = False
	for fieldname, role in ROLE_DEFAULTS.items():
		if not settings.get(fieldname) and frappe.db.exists("Role", role):
			settings.set(fieldname, role)
			changed = True
	if not settings.bill_tracking_invoice_roles:
		for role in BILL_TRACKING_INVOICE_ROLES:
			if frappe.db.exists("Role", role):
				settings.append("bill_tracking_invoice_roles", {"role": role})
				changed = True
	if changed:
		settings.save(ignore_permissions=True)
