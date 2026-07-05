# Copyright (c) 2026, Mohammed Anas and Contributors
# See license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate


EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


def create_item_attribute(attribute_name):
	"""Create an Item Attribute if it doesn't exist."""
	if frappe.db.exists("Item Attribute", attribute_name):
		return frappe.get_doc("Item Attribute", attribute_name)

	doc = frappe.get_doc({
		"doctype": "Item Attribute",
		"attribute_name": attribute_name,
	})
	doc.insert(ignore_permissions=True)
	return doc


def create_item_attribute_value(attribute_name, value):
	"""Create an Item Attribute Value if it doesn't exist."""
	if frappe.db.exists("Item Attribute Value", value):
		return frappe.get_doc("Item Attribute Value", value)

	doc = frappe.get_doc({
		"doctype": "Item Attribute Value",
		"attribute_name": attribute_name,
		"attribute_value": value,
	})
	doc.insert(ignore_permissions=True)
	return doc


def setup_test_attributes():
	"""Create test attributes: Colour (grid), Size (row)."""
	colour_attr = create_item_attribute("Colour")
	size_attr = create_item_attribute("Size")

	for val in ["Red", "Blue", "Green"]:
		create_item_attribute_value("Colour", val)

	for val in ["S", "M", "L"]:
		create_item_attribute_value("Size", val)

	return colour_attr, size_attr


def setup_yrp_settings(colour_as_grid=True):
	"""Configure YRP Settings with production order attributes."""
	settings = frappe.get_doc("YRP Settings")
	settings.set("production_order_attributes", [])

	settings.append("production_order_attributes", {
		"attribute": "Colour",
		"is_grid_attribute": 1 if colour_as_grid else 0,
	})
	settings.append("production_order_attributes", {
		"attribute": "Size",
		"is_grid_attribute": 0,
	})

	settings.po_dependent_attribute = None
	settings.po_dependent_attribute_value = None
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	return settings


def create_test_item(name1="Test PO Item", attributes=None, primary_attribute=None):
	"""Create a test Item with attributes."""
	if not attributes:
		attributes = ["Colour", "Size"]

	# Check if UOM exists
	if not frappe.db.exists("UOM", "Nos"):
		frappe.get_doc({"doctype": "UOM", "uom_name": "Nos"}).insert(ignore_permissions=True)

	# Check if Item Group exists
	if not frappe.db.exists("Item Group", "Test Group"):
		frappe.get_doc({
			"doctype": "Item Group",
			"item_group_name": "Test Group",
		}).insert(ignore_permissions=True)

	doc = frappe.get_doc({
		"doctype": "Item",
		"name1": name1,
		"item_group": "Test Group",
		"default_unit_of_measure": "Nos",
		"primary_attribute": primary_attribute or (attributes[0] if attributes else None),
		"attributes": [{"attribute": attr} for attr in attributes],
	})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	# Add attribute values to mappings
	for attr_row in doc.attributes:
		if attr_row.mapping:
			mapping = frappe.get_doc("Item Item Attribute Mapping", attr_row.mapping)
			if attr_row.attribute == "Colour":
				for val in ["Red", "Blue", "Green"]:
					mapping.append("values", {"attribute_value": val})
			elif attr_row.attribute == "Size":
				for val in ["S", "M", "L"]:
					mapping.append("values", {"attribute_value": val})
			mapping.save(ignore_permissions=True)

	frappe.db.commit()
	return doc


def create_production_term(term_name="Test Term", submit=True):
	"""Create a Production Term with a detail row."""
	if frappe.db.exists("Production Term", term_name):
		doc = frappe.get_doc("Production Term", term_name)
		if submit and doc.docstatus == 0:
			doc.submit()
		return doc

	doc = frappe.get_doc({
		"doctype": "Production Term",
		"term_name": term_name,
		"production_term_details": [
			{"term": "Test term detail"},
		],
	})
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	frappe.db.commit()
	return doc


def create_production_order(
	delivery_date=None,
	dont_deliver_after=None,
	production_term=None,
	item_details=None,
	production_order_details=None,
	do_not_save=False,
	do_not_submit=False,
):
	"""Create a Production Order for testing."""
	today = nowdate()
	delivery_date = delivery_date or add_days(today, 7)
	dont_deliver_after = dont_deliver_after or add_days(today, 14)

	doc = frappe.new_doc("Production Order")
	doc.delivery_date = delivery_date
	doc.dont_deliver_after = dont_deliver_after
	doc.posting_date = today

	if production_term:
		doc.production_term = production_term

	if item_details:
		doc.item_details = json.dumps(item_details) if isinstance(item_details, list) else item_details

	if production_order_details:
		for row in production_order_details:
			doc.append("production_order_details", row)

	if do_not_save:
		return doc

	doc.insert(ignore_permissions=True)

	if not do_not_submit:
		doc.submit()

	return doc


class TestProductionOrder(IntegrationTestCase):
	"""Integration tests for Production Order."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		setup_test_attributes()
		setup_yrp_settings()
		cls.test_item = create_test_item()

	# ──────────────────────────────────────────────
	# Basic Creation & Save
	# ──────────────────────────────────────────────

	def test_create_production_order_draft(self):
		"""Production Order can be created and saved as draft."""
		doc = create_production_order(do_not_submit=True)
		self.assertEqual(doc.docstatus, 0)
		self.assertTrue(doc.name.startswith("PPO-"))

	def test_lead_time_calculated_on_save(self):
		"""lead_time_given is calculated as delivery_date - posting_date on save."""
		today = nowdate()
		doc = create_production_order(
			delivery_date=add_days(today, 10),
			do_not_submit=True,
		)
		self.assertEqual(doc.lead_time_given, 10)

	def test_new_doc_clears_ordered_details(self):
		"""New production order clears production_ordered_details and submission metadata."""
		doc = create_production_order(do_not_submit=True)
		self.assertEqual(len(doc.production_ordered_details), 0)
		self.assertIsNone(doc.submitted_by)
		self.assertIsNone(doc.submitted_time)

	# ──────────────────────────────────────────────
	# Submission Validations
	# ──────────────────────────────────────────────

	def test_submit_sets_posting_date_to_today(self):
		"""On submit, posting_date is set to current date."""
		doc = create_production_order()
		self.assertEqual(doc.posting_date, nowdate())

	def test_submit_sets_submitted_by_and_time(self):
		"""On submit, submitted_by and submitted_time are populated."""
		doc = create_production_order()
		self.assertEqual(doc.submitted_by, frappe.session.user)
		self.assertIsNotNone(doc.submitted_time)

	def test_submit_recalculates_lead_time(self):
		"""On submit, lead_time_given is recalculated based on today's date."""
		today = nowdate()
		doc = create_production_order(delivery_date=add_days(today, 5))
		self.assertEqual(doc.lead_time_given, 5)

	def test_delivery_date_before_posting_date_throws(self):
		"""Cannot submit if delivery_date < posting_date."""
		today = nowdate()
		doc = create_production_order(
			delivery_date=add_days(today, -1),
			do_not_submit=True,
		)
		self.assertRaises(frappe.ValidationError, doc.submit)

	def test_delivery_date_after_dont_deliver_after_throws(self):
		"""Cannot submit if delivery_date > dont_deliver_after."""
		today = nowdate()
		doc = create_production_order(
			delivery_date=add_days(today, 20),
			dont_deliver_after=add_days(today, 10),
			do_not_submit=True,
		)
		self.assertRaises(frappe.ValidationError, doc.submit)

	def test_unsubmitted_production_term_throws(self):
		"""Cannot submit if linked Production Term is not submitted."""
		term = create_production_term(term_name="Draft Term", submit=False)
		doc = create_production_order(
			production_term=term.name,
			do_not_submit=True,
		)
		self.assertRaises(frappe.ValidationError, doc.submit)

	def test_submitted_production_term_allows_submit(self):
		"""Can submit when linked Production Term is submitted."""
		term = create_production_term(term_name="Submitted Term", submit=True)
		doc = create_production_order(production_term=term.name)
		self.assertEqual(doc.docstatus, 1)

	# ──────────────────────────────────────────────
	# Cancellation
	# ──────────────────────────────────────────────

	def test_cancel_without_references(self):
		"""Can cancel a submitted Production Order with no linked references."""
		doc = create_production_order()
		doc.cancel()
		doc.reload()
		self.assertEqual(doc.docstatus, 2)

	def test_cancel_with_linked_references_throws(self):
		"""Cannot cancel if production_ordered_details have reference_name."""
		doc = create_production_order()
		doc.append("production_ordered_details", {
			"reference_doctype": "Production Group",
			"reference_name": "PG-00001",
			"quantity": 10,
		})
		doc.save()
		self.assertRaises(frappe.ValidationError, doc.cancel)

	def test_cancel_with_empty_reference_name_allows(self):
		"""Can cancel if production_ordered_details exist but reference_name is empty."""
		doc = create_production_order()
		doc.append("production_ordered_details", {
			"reference_doctype": "",
			"reference_name": "",
			"quantity": 10,
		})
		doc.save()
		doc.cancel()
		doc.reload()
		self.assertEqual(doc.docstatus, 2)

	# ──────────────────────────────────────────────
	# Update After Submit
	# ──────────────────────────────────────────────

	def test_update_delivery_date_after_submit(self):
		"""Updating delivery_date after submit recalculates lead_time_given."""
		today = nowdate()
		doc = create_production_order(delivery_date=add_days(today, 5))
		self.assertEqual(doc.lead_time_given, 5)

		doc.delivery_date = add_days(today, 12)
		doc.save()
		doc.reload()
		self.assertEqual(doc.lead_time_given, 12)

	# ──────────────────────────────────────────────
	# item_details JSON Parsing
	# ──────────────────────────────────────────────

	def test_item_details_json_creates_order_details(self):
		"""Saving with item_details JSON populates production_order_details child table."""
		item_details = [{
			"item": self.test_item.name,
			"entries": [
				{"attributes": {"Colour": "Red", "Size": "M"}, "qty": 10},
				{"attributes": {"Colour": "Blue", "Size": "L"}, "qty": 20},
			],
		}]

		doc = create_production_order(
			item_details=item_details,
			do_not_submit=True,
		)
		self.assertEqual(len(doc.production_order_details), 2)
		self.assertEqual(doc.production_order_details[0].item, self.test_item.name)
		self.assertEqual(doc.production_order_details[0].quantity, 10)
		self.assertEqual(doc.production_order_details[1].quantity, 20)

	def test_item_details_skips_zero_qty_entries(self):
		"""Entries with qty <= 0 are skipped."""
		item_details = [{
			"item": self.test_item.name,
			"entries": [
				{"attributes": {"Colour": "Red", "Size": "S"}, "qty": 5},
				{"attributes": {"Colour": "Blue", "Size": "M"}, "qty": 0},
			],
		}]

		doc = create_production_order(
			item_details=item_details,
			do_not_submit=True,
		)
		self.assertEqual(len(doc.production_order_details), 1)
		self.assertEqual(doc.production_order_details[0].quantity, 5)

	def test_item_details_creates_variants(self):
		"""Saving with item_details creates Item Variants for each entry."""
		item_details = [{
			"item": self.test_item.name,
			"entries": [
				{"attributes": {"Colour": "Green", "Size": "L"}, "qty": 15},
			],
		}]

		doc = create_production_order(
			item_details=item_details,
			do_not_submit=True,
		)
		row = doc.production_order_details[0]
		self.assertTrue(row.item_variant)
		self.assertTrue(frappe.db.exists("Item Variant", row.item_variant))

	def test_item_details_stores_attributes_json(self):
		"""Each production_order_detail row stores attributes as JSON."""
		item_details = [{
			"item": self.test_item.name,
			"entries": [
				{"attributes": {"Colour": "Red", "Size": "S"}, "qty": 8},
			],
		}]

		doc = create_production_order(
			item_details=item_details,
			do_not_submit=True,
		)
		row = doc.production_order_details[0]
		attr_map = json.loads(row.attributes_json)
		self.assertEqual(attr_map["Colour"], "Red")
		self.assertEqual(attr_map["Size"], "S")

	def test_item_details_empty_group_skipped(self):
		"""Groups without item name are skipped."""
		item_details = [
			{"item": "", "entries": [{"attributes": {"Colour": "Red"}, "qty": 5}]},
			{
				"item": self.test_item.name,
				"entries": [{"attributes": {"Colour": "Red", "Size": "M"}, "qty": 3}],
			},
		]

		doc = create_production_order(
			item_details=item_details,
			do_not_submit=True,
		)
		self.assertEqual(len(doc.production_order_details), 1)

	# ──────────────────────────────────────────────
	# API: get_production_order_settings
	# ──────────────────────────────────────────────

	def test_get_production_order_settings_returns_attributes(self):
		"""get_production_order_settings returns configured attributes."""
		from yrp.yrp.doctype.production_order.production_order import get_production_order_settings

		result = get_production_order_settings()
		self.assertIn("attributes", result)
		self.assertIn("Colour", result["attributes"])
		self.assertIn("Size", result["attributes"])

	def test_get_production_order_settings_returns_grid_attribute(self):
		"""get_production_order_settings returns the grid attribute."""
		from yrp.yrp.doctype.production_order.production_order import get_production_order_settings

		result = get_production_order_settings()
		self.assertEqual(result["grid_attribute"], "Colour")

	# ──────────────────────────────────────────────
	# API: get_item_production_attributes
	# ──────────────────────────────────────────────

	def test_get_item_production_attributes(self):
		"""Returns correct attribute structure for an item."""
		from yrp.yrp.doctype.production_order.production_order import get_item_production_attributes

		result = get_item_production_attributes(self.test_item.name)
		self.assertEqual(result["item"], self.test_item.name)
		self.assertEqual(result["grid_attribute"], "Colour")
		self.assertIn("Red", result["grid_attribute_values"])
		self.assertIn("Size", result["row_attributes"])

	def test_get_item_production_attributes_no_matching_attrs_throws(self):
		"""Throws if item has no matching production order attributes."""
		from yrp.yrp.doctype.production_order.production_order import get_item_production_attributes

		# Create item with non-configured attribute
		other_attr = create_item_attribute("Weight")
		item = frappe.get_doc({
			"doctype": "Item",
			"name1": "No Match Item",
			"item_group": "Test Group",
			"default_unit_of_measure": "Nos",
			"attributes": [{"attribute": "Weight"}],
		})
		item.insert(ignore_permissions=True)
		frappe.db.commit()

		self.assertRaises(frappe.ValidationError, get_item_production_attributes, item.name)

	# ──────────────────────────────────────────────
	# API: get_order_summary
	# ──────────────────────────────────────────────

	def test_get_order_summary_aggregates_by_primary_attribute(self):
		"""Order summary groups quantities by item and primary attribute value."""
		from yrp.yrp.doctype.production_order.production_order import get_order_summary

		item_details = [{
			"item": self.test_item.name,
			"entries": [
				{"attributes": {"Colour": "Red", "Size": "S"}, "qty": 10},
				{"attributes": {"Colour": "Red", "Size": "M"}, "qty": 20},
				{"attributes": {"Colour": "Blue", "Size": "S"}, "qty": 5},
			],
		}]

		doc = create_production_order(item_details=item_details)
		summary = get_order_summary(doc.name)

		self.assertIn(self.test_item.name, summary)
		item_summary = summary[self.test_item.name]
		# Primary attribute is Colour, so aggregation is by Colour
		self.assertEqual(item_summary.get("Red", 0), 30)
		self.assertEqual(item_summary.get("Blue", 0), 5)

	def test_get_order_summary_empty_order(self):
		"""Order summary returns empty dict for order with no details."""
		from yrp.yrp.doctype.production_order.production_order import get_order_summary

		doc = create_production_order()
		summary = get_order_summary(doc.name)
		self.assertEqual(summary, {})

	# ──────────────────────────────────────────────
	# fetch_production_order_items (round-trip)
	# ──────────────────────────────────────────────

	def test_fetch_production_order_items_round_trip(self):
		"""Items saved via JSON can be fetched back into grouped format."""
		from yrp.yrp.doctype.production_order.production_order import fetch_production_order_items

		item_details = [{
			"item": self.test_item.name,
			"entries": [
				{"attributes": {"Colour": "Red", "Size": "M"}, "qty": 7},
				{"attributes": {"Colour": "Blue", "Size": "L"}, "qty": 3},
			],
		}]

		doc = create_production_order(
			item_details=item_details,
			do_not_submit=True,
		)
		doc.reload()

		fetched = fetch_production_order_items(doc)
		self.assertEqual(len(fetched), 1)
		self.assertEqual(fetched[0]["item"], self.test_item.name)
		self.assertEqual(len(fetched[0]["entries"]), 2)

		# Verify quantities match
		qtys = sorted([e["qty"] for e in fetched[0]["entries"]])
		self.assertEqual(qtys, [3.0, 7.0])

	# ──────────────────────────────────────────────
	# Edge Cases
	# ──────────────────────────────────────────────

	def test_delivery_date_equals_posting_date_allowed(self):
		"""delivery_date == posting_date (lead_time = 0) is valid."""
		today = nowdate()
		doc = create_production_order(
			delivery_date=today,
			dont_deliver_after=add_days(today, 5),
		)
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.lead_time_given, 0)

	def test_delivery_date_equals_dont_deliver_after_allowed(self):
		"""delivery_date == dont_deliver_after is valid."""
		today = nowdate()
		target = add_days(today, 5)
		doc = create_production_order(
			delivery_date=target,
			dont_deliver_after=target,
		)
		self.assertEqual(doc.docstatus, 1)

	def test_amend_cancelled_order(self):
		"""A cancelled Production Order can be amended."""
		doc = create_production_order()
		doc.cancel()

		amended = frappe.copy_doc(doc)
		amended.amended_from = doc.name
		amended.docstatus = 0
		amended.insert(ignore_permissions=True)
		self.assertTrue(amended.name)
		self.assertEqual(amended.amended_from, doc.name)
