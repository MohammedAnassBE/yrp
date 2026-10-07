from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp.patches.seed_role_settings import execute as seed_role_settings
from yrp.yrp.doctype.yrp_bill_tracking.yrp_bill_tracking import get_role_permissions
from yrp.yrp.doctype.yrp_purchase_order.yrp_purchase_order import set_open_status

ROLE_FIELDS = {
	"purchase_order_manager_role": "Purchase Manager",
	"bill_tracking_cancel_request_role": "HR User",
	"bill_tracking_cancel_approver_role": "HR Manager",
}


def _set_invoice_roles(roles):
	settings = frappe.get_single("YRP Settings")
	settings.set("bill_tracking_invoice_roles", [{"role": role} for role in roles])
	settings.save(ignore_permissions=True)


class TestYRPSettingsRoles(IntegrationTestCase):
	def test_seed_sets_todays_roles_when_unset(self):
		for fieldname in ROLE_FIELDS:
			frappe.db.set_single_value("YRP Settings", fieldname, None)
		_set_invoice_roles([])

		seed_role_settings()

		settings = frappe.get_single("YRP Settings")
		self.assertEqual({fieldname: settings.get(fieldname) for fieldname in ROLE_FIELDS}, ROLE_FIELDS)
		self.assertEqual(
			sorted(row.role for row in settings.bill_tracking_invoice_roles),
			["Accounts Manager", "Accounts User"],
		)

	def test_seed_keeps_configured_roles(self):
		for fieldname in ROLE_FIELDS:
			frappe.db.set_single_value("YRP Settings", fieldname, "Stock User")
		_set_invoice_roles(["Stock User"])

		seed_role_settings()

		settings = frappe.get_single("YRP Settings")
		for fieldname in ROLE_FIELDS:
			self.assertEqual(settings.get(fieldname), "Stock User")
		self.assertEqual([row.role for row in settings.bill_tracking_invoice_roles], ["Stock User"])

	def test_bill_tracking_permissions_follow_configured_roles(self):
		frappe.db.set_single_value("YRP Settings", "bill_tracking_cancel_request_role", "Stock User")
		frappe.db.set_single_value("YRP Settings", "bill_tracking_cancel_approver_role", "Stock Manager")
		_set_invoice_roles(["Purchase User"])

		with patch("frappe.get_roles", return_value=["Stock User", "Purchase User", "HR Manager"]):
			permissions = get_role_permissions()

		self.assertEqual(
			permissions,
			{"can_request_cancel": True, "can_approve_cancel": False, "can_create_invoice": True},
		)

	def test_purchase_order_open_status_uses_configured_manager_role(self):
		frappe.db.set_single_value("YRP Settings", "purchase_order_manager_role", "Stock User")

		with self.set_user("Guest"):
			with patch("frappe.get_roles", return_value=["Purchase Manager"]):
				self.assertRaises(frappe.PermissionError, set_open_status, "_Test Missing PO", "Close")
			with patch("frappe.get_roles", return_value=["Stock User"]):
				self.assertRaises(frappe.DoesNotExistError, set_open_status, "_Test Missing PO", "Close")
