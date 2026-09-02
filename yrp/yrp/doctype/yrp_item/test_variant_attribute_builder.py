from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.yrp_item.yrp_item import build_variant_attributes


class TestVariantAttributeBuilder(FrappeTestCase):
	def test_keeps_only_attributes_configured_for_stage(self):
		ipd = SimpleNamespace(
			name="IPD-TEST",
			dependent_attribute_mapping="STAGE-MAPPING",
		)
		with patch(
			"yrp.yrp.doctype.yrp_item_dependent_attribute_mapping."
			"item_dependent_attribute_mapping.get_dependent_attribute_details",
			return_value={
				"attribute": "Stage",
				"attr_list": {
					"Pack": {"attributes": ["Size", "Colour"]},
				},
			},
		):
			result = build_variant_attributes(
				{"Size": "M", "Colour": "Navy", "Panel": "Front"},
				"Pack",
				ipd,
			)

		self.assertEqual(
			result,
			{"Stage": "Pack", "Size": "M", "Colour": "Navy"},
		)

	def test_unknown_stage_is_rejected(self):
		ipd = SimpleNamespace(
			name="IPD-TEST",
			dependent_attribute_mapping="STAGE-MAPPING",
		)
		with (
			patch(
				"yrp.yrp.doctype.yrp_item_dependent_attribute_mapping."
				"item_dependent_attribute_mapping.get_dependent_attribute_details",
				return_value={
					"attribute": "Stage",
					"attr_list": {"Pack": {"attributes": ["Size"]}},
				},
			),
			self.assertRaises(frappe.ValidationError),
		):
			build_variant_attributes({"Size": "M"}, "Ironing", ipd)
