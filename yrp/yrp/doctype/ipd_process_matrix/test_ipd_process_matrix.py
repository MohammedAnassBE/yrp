# Copyright (c) 2026, Mohammed Anas and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp.yrp.doctype.ipd_process_matrix.ipd_process_matrix import IPDProcessMatrix
from yrp.yrp.utils.ipd_engine import get_process_io


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]



class IntegrationTestIPDProcessMatrix(IntegrationTestCase):
	"""
	Integration tests for IPDProcessMatrix.
	Use this class for testing interactions between multiple components.
	"""

	def test_grouped_combinations_expose_row_item_override(self):
		matrix = frappe._dict(
			combinations=[
				frappe._dict(
					group_index=0,
					side="Input",
					combo_index=0,
					item="YARN-A",
					quantity=0.6,
					uom="Kg",
					wastage_pct=0,
				),
				frappe._dict(
					group_index=0,
					side="Output",
					combo_index=0,
					item="FABRIC-A",
					quantity=1,
					uom="Kg",
					wastage_pct=0,
				),
			],
			combination_attributes=[],
		)

		group = IPDProcessMatrix.get_combinations_grouped(matrix)[0]

		self.assertEqual(group["input"][0]["item"], "YARN-A")
		self.assertEqual(group["output"][0]["item"], "FABRIC-A")

	def test_process_io_uses_input_and_output_row_item_overrides(self):
		ipd = frappe._dict(
			item="DEFAULT-CLOTH",
			dependent_attribute=None,
			ipd_processes=[],
		)
		matrix = frappe._dict(
			name="MATRIX-1",
			reference_item_variant=None,
			input_item="DEFAULT-INPUT",
			output_item="DEFAULT-OUTPUT",
			get_combinations_grouped=lambda: {
				0: {
					"input": [
						{
							"combo_index": 0,
							"item": "YARN-A",
							"qty": 0.5,
							"uom": "Kg",
							"wastage_pct": 0,
							"attrs": {},
						}
					],
					"output": [
						{
							"combo_index": 0,
							"item": "FABRIC-A",
							"qty": 1,
							"uom": "Kg",
							"wastage_pct": 0,
							"attrs": {"Dia": "24 Dia"},
						}
					],
				}
			},
		)

		def get_doc(doctype, name):
			return ipd if doctype == "Item Production Detail" else matrix

		with (
			patch("yrp.yrp.utils.ipd_engine.frappe.get_all", return_value=["MATRIX-1"]),
			patch("yrp.yrp.utils.ipd_engine.frappe.get_doc", side_effect=get_doc),
		):
			result = get_process_io(
				"IPD-1",
				"Knitting",
				[{"attrs": {"Dia": "24 Dia"}, "qty": 2}],
			)

		self.assertEqual(result["inputs"][0]["item"], "YARN-A")
		self.assertEqual(result["outputs"][0]["item"], "FABRIC-A")
		self.assertEqual(result["inputs"][0]["qty"], 1)
		self.assertEqual(result["outputs"][0]["qty"], 2)
