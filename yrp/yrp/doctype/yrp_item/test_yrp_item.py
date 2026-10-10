from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp.stock.test_uom import _attribute, _ensure_uom, _india_compliance_item_fields, _item_group
from yrp.yrp.doctype.yrp_item.yrp_item import ensure_global_attribute_values, get_or_create_variant

NON_ITEM_MANAGER = "_test_variant_planner@example.com"


class TestGetOrCreateVariant(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		suffix = frappe.generate_hash(length=8)
		cls.attribute = f"_Test Variant Colour {suffix}"
		mapping = _attribute(cls.attribute, f"Red {suffix}")
		mapping_doc = frappe.get_doc("YRP Item Item Attribute Mapping", mapping)
		cls.values = [f"Red {suffix}", f"Blue {suffix}"]
		ensure_global_attribute_values(cls.attribute, cls.values, check_permission=False)
		mapping_doc.append("values", {"attribute_value": cls.values[1]})
		mapping_doc.save(ignore_permissions=True)
		cls.template = _item(
			f"_Test Variant Template {suffix}",
			has_variants=1,
			attributes=[{"attribute": cls.attribute, "mapping": mapping}],
		)
		cls.plain_item = _item(f"_Test Plain Item {suffix}")
		if not frappe.db.exists("User", NON_ITEM_MANAGER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": NON_ITEM_MANAGER,
					"first_name": "Variant",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_user_without_item_create_gets_new_variant_of_template(self):
		frappe.set_user(NON_ITEM_MANAGER)
		self.assertFalse(frappe.has_permission("Item", "create"))

		variant = get_or_create_variant(self.template, {self.attribute: self.values[0]})

		self.assertEqual(frappe.db.get_value("Item", variant, "variant_of"), self.template)

	def test_existing_variant_is_returned_without_insert(self):
		created = get_or_create_variant(self.template, {self.attribute: self.values[1]})
		frappe.set_user(NON_ITEM_MANAGER)

		with patch("frappe.model.document.Document.insert") as insert:
			self.assertEqual(get_or_create_variant(self.template, {self.attribute: self.values[1]}), created)
		insert.assert_not_called()

	def test_non_template_item_is_not_inserted_as_variant(self):
		frappe.set_user(NON_ITEM_MANAGER)

		with self.assertRaises(frappe.ValidationError):
			get_or_create_variant(self.plain_item, {self.attribute: self.values[0]})

	def test_user_without_item_create_cannot_insert_item_directly(self):
		frappe.set_user(NON_ITEM_MANAGER)

		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": f"_Test Direct Item {frappe.generate_hash(length=8)}",
					"item_group": _item_group(),
					"stock_uom": _ensure_uom("Piece"),
					**_india_compliance_item_fields(),
				}
			).insert()


def _item(item_code, **fields):
	return (
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_name": item_code,
				"item_group": _item_group(),
				"stock_uom": _ensure_uom("Piece"),
				"is_stock_item": 1,
				**_india_compliance_item_fields(),
				**fields,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)
