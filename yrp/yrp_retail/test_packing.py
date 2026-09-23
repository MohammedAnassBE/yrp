"""Native non-stock Delivery Notes and Packing Slips, with rollback-only fixtures."""
import unittest

import frappe
from frappe.utils import getdate, today

from yrp.yrp.doctype.yrp_item_master_template.test_yrp_item_master_template import TestCreateItemFromTemplate
from yrp.yrp_retail import packing


class TestPacking(unittest.TestCase):
	def setUp(self):
		TestCreateItemFromTemplate.setUp(self)
		self.addCleanup(frappe.set_user, frappe.session.user)
		suffix = frappe.generate_hash(length=8)
		self.company = frappe.get_doc({"doctype": "Company", "company_name": "Test Carton Company " + suffix,
			"abbr": "TC" + suffix[:3], "country": "United States", "default_currency": "USD",
			"chart_of_accounts": "Standard", "enable_perpetual_inventory": 0}).insert()
		frappe.get_doc({"doctype": "Fiscal Year", "year": "Test Carton Year " + suffix,
			"year_start_date": f"{getdate(today()).year}-01-01", "year_end_date": f"{getdate(today()).year}-12-31",
			"companies": [{"company": self.company.name}]}).insert()
		group = frappe.get_doc({"doctype": "Customer Group", "customer_group_name": "Test Carton Group " + suffix,
			"parent_customer_group": frappe.db.get_value("Customer Group", {"lft": 1}, "name")}).insert()
		territory = frappe.get_doc({"doctype": "Territory", "territory_name": "Test Carton Territory " + suffix,
			"parent_territory": frappe.db.get_value("Territory", {"lft": 1}, "name")}).insert()
		self.customer = frappe.get_doc({"doctype": "Customer", "customer_name": "Test Carton Customer " + suffix,
			"customer_type": "Individual", "customer_group": group.name, "territory": territory.name}).insert()
		self.price_list = frappe.get_doc({"doctype": "Price List", "price_list_name": "Test Carton Prices " + suffix,
			"selling": 1, "enabled": 1, "currency": "USD"}).insert()
		self.item = frappe.get_doc({"doctype": "Item", "item_code": self.item_name,
			"item_name": self.item_name, "item_group": self.group.name, "stock_uom": self.uom.name,
			"gst_hsn_code": self.hsn.name, "is_stock_item": 0,
			"yrp_item_master_template": self.template.name}).insert()
		frappe.get_doc({"doctype": "Item Price", "item_code": self.item.name,
			"uom": self.uom.name, "price_list": self.price_list.name, "price_list_rate": 10}).insert()
		self.dn = self.delivery_note()

	def delivery_note(self, quantities=(4, 6)):
		return frappe.get_doc({"doctype": "Delivery Note", "company": self.company.name,
			"customer": self.customer.name, "posting_date": today(), "currency": "USD", "conversion_rate": 1,
			"selling_price_list": self.price_list.name, "price_list_currency": "USD", "plc_conversion_rate": 1,
			"ignore_pricing_rule": 1, "items": [{"item_code": self.item.name,
				"qty": qty, "uom": self.uom.name, "stock_uom": self.uom.name,
				"conversion_factor": 1, "rate": 10, "price_list_rate": 10} for qty in quantities]}).insert()

	def carton(self, case, rows=None, submit=False, dn=None):
		dn = dn or self.dn
		rows = rows or [{"dn_detail": dn.items[0].name, "qty": dn.items[0].qty}]
		result = packing.create_carton(dn.name, case, rows)
		doc = frappe.get_doc("Packing Slip", result["name"])
		if submit:
			doc.submit()
		return doc

	def packed_delivery(self):
		first = self.carton(1, submit=True)
		second = self.carton(2, [{"dn_detail": self.dn.items[1].name, "qty": 6}], submit=True)
		self.dn.reload()
		self.dn.submit()
		return first, second

	def test_carton_delivery_uses_exact_duplicate_item_rows(self):
		first, second = self.packed_delivery()
		result = packing.mark_packing_slip_delivered(first.name)
		self.assertEqual(result["delivered_qty"], 4)
		self.assertEqual(result["per_delivered"], 40)
		self.assertEqual(result["total_cartons"], 2)
		self.assertEqual(result["delivered_cartons"], 1)
		self.dn.reload()
		self.assertEqual([row.yrp_delivered_qty for row in self.dn.items], [4, 0])
		stamp = frappe.db.get_value("Packing Slip", first.name, "yrp_delivered_at")
		packing.mark_packing_slip_delivered(first.name)
		self.assertEqual(frappe.db.get_value("Packing Slip", first.name, "yrp_delivered_at"), stamp)
		self.assertEqual(packing.mark_delivery_note_delivered(self.dn.name)["per_delivered"], 100)
		self.assertEqual(packing.mark_packing_slip_delivered(second.name, False)["per_delivered"], 40)

	def test_draft_cartons_reserve_numbers_and_quantities(self):
		self.carton(1)
		with self.assertRaises(frappe.ValidationError):
			self.carton(1, [{"dn_detail": self.dn.items[1].name, "qty": 1}])
		with self.assertRaises(frappe.ValidationError):
			self.carton(2, [{"dn_detail": self.dn.items[0].name, "qty": 1}])
		self.assertEqual(frappe.db.count("Packing Slip", {"delivery_note": self.dn.name}), 1)

	def test_other_dn_rows_and_invalid_quantities_are_rejected(self):
		other = self.delivery_note()
		with self.assertRaises(frappe.ValidationError):
			self.carton(1, [{"dn_detail": other.items[0].name, "qty": 1}])
		for qty in (0, -1, float("nan"), float("inf")):
			with self.assertRaises((frappe.ValidationError, ValueError)):
				self.carton(1, [{"dn_detail": self.dn.items[0].name, "qty": qty}])

	def test_delivery_requires_submitted_documents_and_complete_coverage(self):
		first = self.carton(1, submit=True)
		with self.assertRaises(frappe.ValidationError):
			packing.mark_packing_slip_delivered(first.name)
		self.dn.reload()
		self.dn.submit()
		with self.assertRaises(frappe.ValidationError):
			packing.mark_delivery_note_delivered(self.dn.name)
		self.assertEqual(frappe.db.get_value("Packing Slip", first.name, "yrp_delivered"), 0)

	def test_delivered_cancellation_is_blocked_until_unmarked(self):
		first, _ = self.packed_delivery()
		packing.mark_packing_slip_delivered(first.name)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc("Packing Slip", first.name).cancel()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc("Delivery Note", self.dn.name).cancel()
		packing.mark_packing_slip_delivered(first.name, False)
		frappe.get_doc("Packing Slip", first.name).cancel()
		self.dn.reload()
		self.assertEqual(self.dn.yrp_delivered_qty, 0)

	def test_raw_status_updates_and_guest_calls_are_denied(self):
		first, _ = self.packed_delivery()
		first.reload()
		first.yrp_delivered = 1
		with self.assertRaises(frappe.PermissionError):
			first.save()
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			packing.mark_packing_slip_delivered(first.name)

	def test_packed_dn_row_identity_and_capacity_are_frozen(self):
		self.carton(1)
		self.dn.reload()
		self.dn.items[0].qty = 3
		with self.assertRaises(frappe.ValidationError):
			self.dn.save()
		self.dn.reload()
		self.dn.remove(self.dn.items[0])
		with self.assertRaises(frappe.ValidationError):
			self.dn.save()
