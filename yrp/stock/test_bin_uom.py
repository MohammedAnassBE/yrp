import frappe
from frappe.tests import IntegrationTestCase

from yrp.stock.utils import get_or_make_bin
from yrp.yrp.doctype.yrp_item.yrp_item import get_parent_item
from yrp.yrp_stock.test_engine_verification import ACCEPTED_DIMS, _wh


def _variant_with_non_default_uom(default_uom):
	item = frappe.db.sql(
		"""
		SELECT variant.name
		FROM `tabItem` variant
		INNER JOIN `tabItem` parent ON parent.name = variant.variant_of
		WHERE parent.is_stock_item = 1 AND parent.stock_uom != %s
		ORDER BY variant.creation
		LIMIT 1
		""",
		default_uom,
	)
	if not item:
		frappe.throw("No stock Item Variant with a non-default stock UOM found.")
	return item[0][0]


class TestBinUOM(IntegrationTestCase):
	def test_new_bin_uses_item_stock_uom_not_global_default(self):
		frappe.db.set_default("stock_uom", "Nos")
		item_code = _variant_with_non_default_uom("Nos")
		expected_uom = frappe.db.get_value("Item", get_parent_item(item_code), "stock_uom")

		bin_name = get_or_make_bin(item_code, _wh("BinUOM"), **ACCEPTED_DIMS)

		self.assertEqual(frappe.db.get_value("YRP Bin", bin_name, "stock_uom"), expected_uom)
