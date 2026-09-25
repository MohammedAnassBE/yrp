"""Native non-stock Delivery Notes and Packing Slips, with rollback-only fixtures."""
import unittest
from unittest.mock import patch

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

	def mixed_uom_delivery(self, unit_qty=12):
		"""Create one boxed row and a configurable individual-unit row."""
		box = frappe.get_doc({"doctype": "UOM",
			"uom_name": "Test Carton Box " + frappe.generate_hash(length=8)}).insert()
		self.item.append("uoms", {"uom": box.name, "conversion_factor": 12})
		self.item.save()
		self.dn.items[0].update({"uom": box.name, "conversion_factor": 12,
			"qty": 1, "rate": 120, "price_list_rate": 120})
		self.dn.items[1].qty = unit_qty
		self.dn.save()
		first = self.carton(1, submit=True)
		second = self.carton(2, [{"dn_detail": self.dn.items[1].name, "qty": unit_qty}], submit=True)
		self.dn.reload()
		self.dn.submit()
		return first, second, box

	def test_mixed_uom_delivery_progress_uses_default_units(self):
		first, second, _ = self.mixed_uom_delivery()
		result = packing.mark_packing_slip_delivered(first.name)
		self.assertEqual((result["delivered_qty"], result["per_delivered"]), (12, 50))
		self.dn.reload()
		self.assertEqual([row.yrp_delivered_qty for row in self.dn.items], [1, 0])
		result = packing.mark_packing_slip_delivered(second.name)
		self.assertEqual((result["delivered_qty"], result["per_delivered"]), (24, 100))
		result = packing.mark_packing_slip_delivered(first.name, False)
		self.assertEqual((result["delivered_qty"], result["per_delivered"]), (12, 50))
		self.dn.reload()
		self.assertEqual([row.yrp_delivered_qty for row in self.dn.items], [0, 12])
		result = packing.mark_packing_slip_delivered(second.name, False)
		self.assertEqual((result["delivered_qty"], result["per_delivered"]), (0, 0))

	def test_delivery_progress_preserves_recorded_conversion(self):
		first, _, box = self.mixed_uom_delivery()
		self.item.reload()
		for row in self.item.uoms:
			if row.uom == box.name:
				row.conversion_factor = 24
		self.item.save()
		result = packing.mark_packing_slip_delivered(first.name)
		self.assertEqual((result["delivered_qty"], result["per_delivered"]), (12, 50))
		self.dn.reload()
		self.assertEqual(self.dn.items[0].conversion_factor, 12)
		self.assertEqual(self.dn.items[0].stock_qty, 12)
		self.assertEqual(self.dn.items[0].yrp_delivered_qty, 1)

	def test_whole_carton_quantity_accepts_field_precision_noise(self):
		self.uom.must_be_whole_number = 1
		self.uom.save()
		carton = self.carton(1, [{"dn_detail": self.dn.items[0].name,
			"qty": 1.0000000000000002}], submit=True)
		self.carton(2, [{"dn_detail": self.dn.items[0].name, "qty": 3},
			{"dn_detail": self.dn.items[1].name, "qty": 6}], submit=True)
		self.dn.reload()
		self.dn.submit()
		result = packing.mark_packing_slip_delivered(carton.name)
		self.assertEqual((result["delivered_qty"], result["per_delivered"]), (1, 10))

	def test_default_unit_backfill_is_idempotent_without_modified_changes(self):
		from yrp.patches import refresh_delivery_progress_default_uom as backfill

		first, _, _ = self.mixed_uom_delivery(unit_qty=24)
		packing.mark_packing_slip_delivered(first.name)
		# Simulate the old mixed-transaction-UOM header and a stale child counter.
		frappe.db.set_value("Delivery Note", self.dn.name,
			{"yrp_delivered_qty": 1, "yrp_per_delivered": 4}, update_modified=False)
		frappe.db.set_value("Delivery Note Item", self.dn.items[0].name,
			"yrp_delivered_qty", 99, update_modified=False)
		self.dn.reload()
		modified = self.dn.modified
		row_modified = [row.modified for row in self.dn.items]
		# Scope patch execution to this test's synthetic document only.
		with patch.object(backfill, "_candidate_names", return_value=[self.dn.name]):
			backfill.execute()
			self.dn.reload()
			self.assertEqual(self.dn.yrp_delivered_qty, 12)
			self.assertAlmostEqual(self.dn.yrp_per_delivered, 100 / 3, places=8)
			self.assertEqual([row.yrp_delivered_qty for row in self.dn.items], [1, 0])
			self.assertEqual(self.dn.modified, modified)
			self.assertEqual([row.modified for row in self.dn.items], row_modified)
			frappe.db.set_value("Delivery Note", self.dn.name,
				"yrp_delivered_qty", 12.0001, update_modified=False)
			backfill.execute()
			self.dn.reload()
			self.assertEqual(self.dn.yrp_delivered_qty, 12)
			with patch.object(frappe.db, "set_value", wraps=frappe.db.set_value) as update:
				backfill.execute()
				update.assert_not_called()

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
