import unittest
from unittest.mock import patch

import frappe

from yrp.yrp_partner import customer_api, sales_order


class TestNativeSalesOrderGuard(unittest.TestCase):
	"""Partners work on YRP sales documents; native Sales Order processing stays closed to them."""

	def test_partner_cannot_close_or_reopen_native_orders(self):
		mixins = frappe.get_hooks("extend_doctype_class").get("Sales Order", [])
		self.assertIn("yrp.yrp_partner.sales_order.PartnerSalesOrderMixin", mixins)
		with patch.object(sales_order, "is_partner", return_value=True):
			with self.assertRaises(frappe.PermissionError):
				sales_order.PartnerSalesOrderMixin().update_status("Closed")

	def test_partner_cannot_open_native_order_calendar(self):
		overrides = frappe.get_hooks("override_whitelisted_methods")
		self.assertEqual(
			overrides.get("erpnext.selling.doctype.sales_order.sales_order.get_events"),
			["yrp.yrp_partner.sales_order.get_events"],
		)
		with patch.object(sales_order, "is_partner", return_value=True):
			with self.assertRaises(frappe.PermissionError):
				sales_order.get_events("2026-01-01", "2026-01-31")

	def test_staff_keep_native_order_calendar(self):
		with (
			patch.object(sales_order, "is_partner", return_value=False),
			patch(
				"erpnext.selling.doctype.sales_order.sales_order.get_events", return_value=["row"]
			) as native,
		):
			self.assertEqual(sales_order.get_events("2026-01-01", "2026-01-31", "[]"), ["row"])
		native.assert_called_once_with("2026-01-01", "2026-01-31", "[]")

	def test_foreign_party_balance_lookup_is_blocked(self):
		self.assertIn(
			"erpnext.accounts.doctype.exchange_rate_revaluation.exchange_rate_revaluation.get_account_details",
			customer_api.UNSAFE_RPC,
		)


class TestRetiredNativeSalesPermissions(unittest.TestCase):
	def test_patch_removes_only_retired_partner_grants(self):
		from yrp.patches import remove_native_sales_partner_permissions as patch_module

		with (
			patch.object(frappe.db, "get_all", return_value=["8j4rbvtkfv"]) as lookup,
			patch.object(frappe, "delete_doc") as delete,
		):
			patch_module.execute()
		filters = lookup.call_args.kwargs["filters"]
		self.assertEqual(filters["parent"], ["in", ["Sales Order", "Delivery Note", "Packing Slip"]])
		self.assertEqual(set(filters["role"][1]), {"YRP Customer", "YRP Sales Partner", "YRP Sales Person"})
		delete.assert_called_once_with("Custom DocPerm", "8j4rbvtkfv", ignore_permissions=True, force=True)
