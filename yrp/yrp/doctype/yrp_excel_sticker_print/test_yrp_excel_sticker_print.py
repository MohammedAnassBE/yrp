import json
from unittest.mock import call, patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp.yrp.doctype.yrp_excel_sticker_print.yrp_excel_sticker_print import (
	get_print_format,
	get_raw_code,
)


class TestExcelStickerPrint(IntegrationTestCase):
	def test_preview_reads_the_yrp_zpl_print_format(self):
		doc = frappe._dict(
			print_format="ZPL-Test",
			serialized_data=json.dumps([{"sku": "SKU-1", "print_quantity": 4}]),
		)
		print_format = frappe._dict(
			width=4,
			height=6,
			zpl_raw_print_format_details=[
				frappe._dict(printer_type="200dpi", raw_code="200-template"),
				frappe._dict(printer_type="300dpi", raw_code="300-template"),
			],
		)

		with (
			patch.object(frappe, "get_doc", side_effect=[doc, print_format]) as get_doc,
			patch.object(frappe, "render_template", return_value="rendered-zpl") as render,
		):
			result = get_raw_code("ESP-0001")

		self.assertEqual(
			get_doc.call_args_list,
			[
				call('YRP Excel Sticker Print', "ESP-0001"),
				call('YRP ZPL Raw Print Format', "ZPL-Test"),
			],
		)
		render.assert_called_once_with(
			"300-template",
			{"print_quantity": 4, "dpi": 300, "sku": "SKU-1"},
		)
		self.assertEqual(
			result,
			{"code": "rendered-zpl", "height": 6, "width": 4},
		)

	def test_print_output_uses_zpl_details_and_labels_per_row(self):
		doc = frappe._dict(
			print_format="ZPL-Test",
			serialized_data=json.dumps(
				[
					{"sku": "SKU-1", "print_quantity": 3},
					{"sku": "SKU-2", "print_quantity": 0},
				]
			),
		)
		print_format = frappe._dict(
			labels_per_row=2,
			zpl_raw_print_format_details=[
				frappe._dict(printer_type="200dpi", raw_code="200-template")
			],
		)

		with (
			patch.object(frappe, "get_doc", side_effect=[doc, print_format]) as get_doc,
			patch.object(frappe, "render_template", return_value="rendered-zpl") as render,
		):
			result = get_print_format("ESP-0001", "200dpi")

		self.assertEqual(
			get_doc.call_args_list,
			[
				call('YRP Excel Sticker Print', "ESP-0001"),
				call('YRP ZPL Raw Print Format', "ZPL-Test"),
			],
		)
		render.assert_called_once_with(
			"200-template",
			{"dpi": 203, "sku": "SKU-1", "print_quantity": 2},
		)
		self.assertEqual(result, "rendered-zpl")

	def test_client_uses_only_yrp_server_methods(self):
		client_path = frappe.get_app_path(
			"yrp", "yrp", "doctype", "yrp_excel_sticker_print", "yrp_excel_sticker_print.js"
		)
		with open(client_path, encoding="utf-8") as client_file:
			client = client_file.read()

		self.assertIn(
			"yrp.yrp.doctype.yrp_excel_sticker_print.yrp_excel_sticker_print.get_raw_code",
			client,
		)
		self.assertIn(
			"yrp.yrp.doctype.yrp_excel_sticker_print.yrp_excel_sticker_print.get_print_format",
			client,
		)
