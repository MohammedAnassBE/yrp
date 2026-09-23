"""Create Item uses fictional fixtures and rolls back every test."""
import secrets
import unittest
from unittest.mock import patch

import frappe

from yrp.yrp.doctype.yrp_item_master_template.yrp_item_master_template import (
	create_item_from_template,
)


class TestCreateItemFromTemplate(unittest.TestCase):
	def setUp(self):
		if not frappe.get_meta("Item").has_field("gst_hsn_code"):
			self.skipTest("HSN/SAC integration is not installed")
		self.savepoint = "item_template_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(self.savepoint)
		self.addCleanup(frappe.db.rollback, save_point=self.savepoint)
		# Exercise mandatory HSN validation regardless of the site's GST settings.
		settings = patch(
			"india_compliance.gst_india.doctype.gst_hsn_code.gst_hsn_code.get_hsn_settings",
			return_value=(True, [8]),
		)
		settings.start()
		self.addCleanup(settings.stop)
		self.item_name = "Test Product " + frappe.generate_hash(length=10)
		self.uom = frappe.get_doc({
			"doctype": "UOM", "uom_name": "Test Unit " + frappe.generate_hash(length=10),
		}).insert()
		self.group = frappe.get_doc({
			"doctype": "Item Group",
			"item_group_name": "Test Group " + frappe.generate_hash(length=10),
			"parent_item_group": frappe.db.get_value("Item Group", {"lft": 1}, "name"),
		}).insert()
		self.hsn = frappe.get_doc({
			"doctype": "GST HSN Code",
			"hsn_code": str(secrets.randbelow(90_000_000) + 10_000_000),
			"description": "Fictional test classification",
		}).insert()
		self.template = frappe.get_doc({
			"doctype": "YRP Item Master Template",
			"name": "Test Template " + frappe.generate_hash(length=10),
			"default_unit_of_measure": self.uom.name,
		}).insert()

	def test_creates_item_with_selected_hsn(self):
		name = create_item_from_template(
			self.template.name, self.item_name, self.group.name, self.hsn.name,
		)
		item = frappe.get_doc("Item", name)
		self.assertEqual(item.gst_hsn_code, self.hsn.name)
		self.assertEqual(item.item_group, self.group.name)
		self.assertEqual(item.stock_uom, self.uom.name)

	def test_template_has_no_hsn_field_and_saves_without_hsn(self):
		self.assertFalse(self.template.meta.has_field("gst_hsn_code"))
		self.template.save()
		self.template.reload()
		self.assertEqual(self.template.default_unit_of_measure, self.uom.name)

	def test_item_still_requires_hsn_when_compliance_requires_it(self):
		with self.assertRaises(frappe.MandatoryError):
			create_item_from_template(self.template.name, self.item_name, self.group.name)
		self.assertFalse(frappe.db.exists("Item", self.item_name))

	def test_unknown_hsn_does_not_create_item(self):
		with self.assertRaises(frappe.LinkValidationError):
			create_item_from_template(
				self.template.name, self.item_name, self.group.name,
				"Missing " + frappe.generate_hash(length=10),
			)
		self.assertFalse(frappe.db.exists("Item", self.item_name))
