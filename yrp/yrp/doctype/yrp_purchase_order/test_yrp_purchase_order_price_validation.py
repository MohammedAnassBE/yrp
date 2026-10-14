from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe
from essdee_yrp.fabric_store import web_workspace
from essdee_yrp.fabric_store.documents.purchase_order import save_purchase_order_document
from frappe.utils import add_days, flt, nowdate

from yrp.attribute_values import ensure_value_master
from yrp.stock.test_uom import _ensure_uom, _india_compliance_item_fields, _item_group
from yrp.yrp.doctype.yrp_item.yrp_item import ensure_global_attribute_values
from yrp.yrp.doctype.yrp_purchase_order import yrp_purchase_order as purchase_order


class TestPurchaseOrderPriceValidation(TestCase):
	def test_missing_price_returns_draft_warning_without_throwing(self):
		rows = [frappe._dict(item_variant="YARN-NAVY", qty=500)]

		with (
			patch.object(purchase_order, "_get_parent_item", return_value="60's GL Yarn"),
			patch.object(purchase_order, "_active_price_names", return_value=[]),
			patch(
				"yrp.yrp.doctype.yrp_item_price.yrp_item_price.get_active_price",
				return_value=None,
			) as get_active_price,
		):
			warnings = purchase_order.validate_price_details(
				rows,
				supplier="S-0010",
				strict=False,
			)

		self.assertEqual(len(warnings), 1)
		self.assertIn("60's GL Yarn", warnings[0])
		self.assertIn("cannot be submitted", warnings[0])
		get_active_price.assert_called_once_with("60's GL Yarn", "S-0010", raise_error=False)

	def test_whole_draft_preview_matches_save_grouping_and_restores_setting(self):
		price = self.make_price(
			[
				{"moq": 0, "price": 100, "lead_time": 3},
				{"moq": 10, "price": 80, "lead_time": 7},
			]
		)
		original = frappe.db.get_single_value("YRP Settings", "enable_price_validation")
		try:
			for enabled in (0, 1):
				frappe.db.set_single_value("YRP Settings", "enable_price_validation", enabled)
				rows = [
					frappe._dict(item_code="ITEM-A", qty=6),
					frappe._dict(item_code="ITEM-B", qty=6),
				]
				with patch(
					"yrp.yrp.doctype.yrp_item_price.yrp_item_price.get_active_price",
					return_value=price,
				):
					preview = purchase_order.apply_price_details(
						rows,
						"SUPPLIER-1",
						strict=False,
						preview_context=[
							{"parent_item": "ITEM", "attributes": {}},
							{"parent_item": "ITEM", "attributes": {}},
						],
					)

				self.assertEqual([row.rate for row in rows], [80, 80])
				self.assertEqual([row["rate"] for row in preview["rows"]], [80, 80])
				self.assertEqual([row["lead_time_days"] for row in preview["rows"]], [7, 7])
				self.assertEqual(preview["rows"][0]["tier"]["moq"], 10)

			rows = [frappe._dict(item_code="ITEM-A", qty=6)]
			with patch(
				"yrp.yrp.doctype.yrp_item_price.yrp_item_price.get_active_price",
				return_value=price,
			):
				preview = purchase_order.apply_price_details(
					rows,
					"SUPPLIER-1",
					strict=False,
					preview_context=[{"parent_item": "ITEM", "attributes": {}}],
				)
			self.assertEqual(rows[0].rate, 100)
			self.assertEqual(preview["rows"][0]["lead_time_days"], 3)
		finally:
			frappe.db.set_single_value("YRP Settings", "enable_price_validation", original)

		self.assertEqual(
			frappe.db.get_single_value("YRP Settings", "enable_price_validation"),
			original,
		)

	@staticmethod
	def make_price(tiers, attribute=None):
		return TestPurchaseOrderPriceValidationAdditional.make_price(
			tiers, attribute=attribute
		)


class TestPurchaseOrderPricePreviewSaveParity(TestCase):
	@classmethod
	def setUpClass(cls):
		cls.previous_user = frappe.session.user
		frappe.set_user("Administrator")
		cls.class_savepoint = "po_price_parity_" + frappe.generate_hash(length=8)
		frappe.db.savepoint(cls.class_savepoint)
		cls.class_savepoint_active = True
		cls.addClassCleanup(cls._cleanup_class_fixtures)
		cls.original_validation_setting = frappe.db.get_single_value(
			"YRP Settings", "enable_price_validation"
		)
		cls.token = "_Test PO Price " + frappe.generate_hash(length=8)
		cls.uom = _ensure_uom(f"{cls.token} Unit")
		cls.item_group = _item_group()
		cls.supplier_a = cls._make_supplier("A")
		cls.supplier_b = cls._make_supplier("B")
		cls.tax = cls._tax_slab()
		cls.legacy_tax = cls._tax_slab("17")
		warehouse = frappe.db.get_value(
			"Warehouse",
			{"is_group": 0, "disabled": 0},
			["name", "company"],
			as_dict=True,
		)
		if not warehouse:
			raise AssertionError("A leaf Warehouse is required for PO pricing integration tests.")
		cls.warehouse = warehouse.name
		cls.company = warehouse.company
		cls.currency = frappe.db.get_value("Company", cls.company, "default_currency")
		cls.order_date = nowdate()
		cls.required_by = add_days(cls.order_date, 20)

		cls.plain_item = cls._make_item("Plain")
		cls.colour, colour_links = cls._make_attribute("Colour", ["Red", "Blue"])
		cls.size, size_links = cls._make_attribute("Size", ["S", "M"])
		cls.attribute_item = cls._make_item(
			"Attribute",
			attributes=[
				{"attribute": cls.colour, "mapping": colour_links["mapping"]},
				{"attribute": cls.size, "mapping": size_links["mapping"]},
			],
			primary_attribute=cls.size,
		)
		cls.primary_item = cls._make_item(
			"Primary",
			attributes=[{"attribute": cls.size, "mapping": size_links["mapping"]}],
			primary_attribute=cls.size,
		)

		cls._approve_price(
			cls.plain_item,
			cls.supplier_a,
			[
				{"moq": 0, "price": 100, "lead_time": 3},
				{"moq": 10, "price": 80, "lead_time": 7},
			],
		)
		cls._approve_price(
			cls.plain_item,
			cls.supplier_b,
			[{"moq": 0, "price": 130, "lead_time": 4}],
		)
		cls._approve_price(
			cls.attribute_item,
			cls.supplier_a,
			[
				{"moq": 0, "price": 110, "lead_time": 2, "attribute_value": colour_links["Red"]},
				{"moq": 0, "price": 120, "lead_time": 4, "attribute_value": colour_links["Blue"]},
			],
			attribute=cls.colour,
		)
		cls._approve_price(
			cls.primary_item,
			cls.supplier_a,
			[
				{"moq": 0, "price": 140, "lead_time": 3, "attribute_value": size_links["S"]},
				{"moq": 10, "price": 150, "lead_time": 5, "attribute_value": size_links["M"]},
			],
			attribute=cls.size,
		)

	@classmethod
	def tearDownClass(cls):
		cls._cleanup_class_fixtures()

	@classmethod
	def _cleanup_class_fixtures(cls):
		if not getattr(cls, "class_savepoint_active", False):
			return
		frappe.db.rollback(save_point=cls.class_savepoint)
		cls.class_savepoint_active = False
		leftovers = frappe.get_all("Item", filters={"name": ["like", f"%{cls.token}%"]}, pluck="name")
		frappe.set_user(cls.previous_user)
		if leftovers:
			raise AssertionError(f"PO price parity fixtures leaked: {leftovers}")

	def setUp(self):
		self.test_savepoint = "po_price_case_" + frappe.generate_hash(length=8)
		frappe.db.savepoint(self.test_savepoint)

	def tearDown(self):
		frappe.db.set_single_value(
			"YRP Settings", "enable_price_validation", self.original_validation_setting
		)
		frappe.db.rollback(save_point=self.test_savepoint)

	@classmethod
	def _make_supplier(cls, label):
		name = f"{cls.token} Supplier {label}"
		return frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": name,
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
			}
		).insert(ignore_permissions=True).name

	@classmethod
	def _tax_slab(cls, percentage=None):
		if percentage:
			if frappe.db.exists("YRP Tax Slab", percentage):
				return percentage
			return frappe.get_doc(
				{"doctype": "YRP Tax Slab", "percentage": percentage, "enabled": 1}
			).insert(ignore_permissions=True).name
		name = frappe.db.get_value("YRP Tax Slab", {"enabled": 1}, "name")
		if name:
			return name
		return frappe.get_doc(
			{"doctype": "YRP Tax Slab", "percentage": "5", "enabled": 1}
		).insert(ignore_permissions=True).name

	@classmethod
	def _make_attribute(cls, label, values):
		attribute = f"{cls.token} {label}"
		frappe.get_doc(
			{"doctype": "Item Attribute", "attribute_name": attribute}
		).insert(ignore_permissions=True)
		ensure_global_attribute_values(attribute, values, check_permission=False)
		links = {value: ensure_value_master(attribute, value) for value in values}
		mapping = frappe.get_doc(
			{
				"doctype": "YRP Item Item Attribute Mapping",
				"attribute_name": attribute,
				"values": [{"attribute_value": links[value]} for value in values],
			}
		).insert(ignore_permissions=True)
		return attribute, {**links, "mapping": mapping.name}

	@classmethod
	def _make_item(cls, label, *, attributes=None, primary_attribute=None):
		name = f"{cls.token} {label}"
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": name,
				"item_name": name,
				"item_group": cls.item_group,
				"stock_uom": cls.uom,
				"is_stock_item": 1,
				"is_purchase_item": 1,
				"is_sales_item": 0,
				"has_variants": int(bool(attributes)),
				"variant_based_on": "Item Attribute" if attributes else "",
				"primary_attribute": primary_attribute,
				"attributes": attributes or [],
				**_india_compliance_item_fields(),
			}
		).insert(ignore_permissions=True).name

	@classmethod
	def _approve_price(cls, item, supplier, tiers, *, attribute=None):
		doc = frappe.get_doc(
			{
				"doctype": "YRP Item Price",
				"item_name": item,
				"supplier": supplier,
				"uom": cls.uom,
				"from_date": cls.order_date,
				"tax": cls.tax,
				"depends_on_attribute": int(bool(attribute)),
				"attribute": attribute,
				"item_price_values": tiers,
			}
		).insert(ignore_permissions=True)
		doc.submit()
		return doc.name

	def test_plain_split_rows_moq_deletion_and_supplier_change_match_reload(self):
		for enabled in (0, 1):
			with self.subTest(enable_price_validation=enabled):
				groups = self._plain_groups(two_rows=True)
				doc = self._preview_save_reload(groups, self.supplier_a, enabled=enabled)
				self.assertEqual([flt(row.rate) for row in doc.items], [80, 80])

				groups = self._plain_groups(two_rows=False)
				doc = self._preview_save_reload(
					groups,
					self.supplier_a,
					enabled=enabled,
					name=doc.name,
					expected_modified=doc.modified,
				)
				self.assertEqual(len(doc.items), 1)
				self.assertEqual(flt(doc.items[0].rate), 100)

				doc = self._preview_save_reload(
					groups,
					self.supplier_b,
					enabled=enabled,
					name=doc.name,
					expected_modified=doc.modified,
				)
				self.assertEqual(doc.supplier, self.supplier_b)
				self.assertEqual(flt(doc.items[0].rate), 130)

	def test_attribute_and_primary_attribute_prices_match_reload_with_lead_time_modes(self):
		for enabled in (0, 1):
			with self.subTest(enable_price_validation=enabled):
				attribute_groups = self._attribute_groups()
				attribute_doc = self._preview_save_reload(
					attribute_groups,
					self.supplier_a,
					enabled=enabled,
					expected_lead_times=[2, 4],
				)
				self.assertEqual(
					[str(row.schedule_date) for row in attribute_doc.items],
					[add_days(self.order_date, 2), add_days(self.order_date, 15)],
				)

				primary_groups = self._primary_attribute_groups()
				primary_doc = self._preview_save_reload(
					primary_groups,
					self.supplier_a,
					enabled=enabled,
					expected_lead_times=[5],
				)
				self.assertEqual(
					[str(row.schedule_date) for row in primary_doc.items],
					[add_days(self.order_date, 5), add_days(self.order_date, 5)],
				)

	def test_mixed_primary_values_preserve_unresolved_rate_and_tax_after_reload(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		groups = [{
			"attributes": [],
			"primary_attribute": self.size,
			"primary_attribute_values": ["S", "M"],
			"items": [self._item_entry(
				self.primary_item,
				{
					"S": {"qty": 3, "rate": 999, "tax": self.legacy_tax},
					"M": {"qty": 4, "rate": 175, "tax": self.legacy_tax},
				},
				primary_attribute=self.size,
				tax=self.legacy_tax,
			)],
		}]
		preview = web_workspace.preview_purchase_order_prices(groups, self.supplier_a)
		preview_row = preview["rows"][0]
		self.assertEqual(preview_row["values"]["S"]["status"], "ok")
		self.assertEqual(preview_row["values"]["M"]["status"], "no_slab")
		groups[0]["items"][0]["values"]["S"].update(
			{"rate": preview_row["values"]["S"]["rate"], "tax": preview_row["values"]["S"]["tax"]}
		)

		doc = self._save_reload(groups, self.supplier_a, enabled=0)
		self.assertEqual([flt(row.rate) for row in doc.items], [140, 175])
		self.assertEqual([str(row.tax) for row in doc.items], [str(self.tax), self.legacy_tax])
		reloaded = group_items_for_ui(doc.items, "Purchase Order")
		values = reloaded[0]["items"][0]["values"]
		self.assertEqual(flt(values["S"]["rate"]), 140)
		self.assertEqual(str(values["S"]["tax"]), str(self.tax))
		self.assertEqual(flt(values["M"]["rate"]), 175)
		self.assertEqual(values["M"]["tax"], self.legacy_tax)

		groups[0]["items"][0]["values"]["M"]["tax"] = ""
		untaxed_doc = self._save_reload(groups, self.supplier_a, enabled=0)
		self.assertEqual([str(row.tax or "") for row in untaxed_doc.items], [str(self.tax), ""])
		untaxed_reload = group_items_for_ui(untaxed_doc.items, "Purchase Order")
		untaxed_values = untaxed_reload[0]["items"][0]["values"]
		self.assertEqual(flt(untaxed_values["M"]["rate"]), 175)
		self.assertEqual(untaxed_values["M"]["tax"], "")

	def _preview_save_reload(
		self,
		groups,
		supplier,
		*,
		enabled,
		name=None,
		expected_modified=None,
		expected_lead_times=None,
	):
		original = frappe.db.get_single_value("YRP Settings", "enable_price_validation")
		frappe.db.set_single_value("YRP Settings", "enable_price_validation", enabled)
		try:
			preview = web_workspace.preview_purchase_order_prices(groups, supplier, name)
			expected = []
			preview_rows = {
				(row["group_index"], row["item_index"]): row for row in preview["rows"]
			}
			actual_lead_times = []
			for group_index, group in enumerate(groups):
				for item_index, item in enumerate(group.get("items") or []):
					preview_row = preview_rows[(group_index, item_index)]
					actual_lead_times.append(preview_row["lead_time_days"])
					if item.get("fetch_delivery_date"):
						self.assertIsNotNone(preview_row["lead_time_days"])
						item["schedule_date"] = add_days(
							self.order_date, preview_row["lead_time_days"]
						)
						item["expected_delivery_date"] = item["schedule_date"]
					for value_key, value in (item.get("values") or {}).items():
						if flt(value.get("qty")) <= 0:
							continue
						value_result = preview_row["values"][value_key]
						self.assertEqual(value_result["status"], "ok")
						expected.append(
							(
								flt(value_result["rate"]),
								str(value_result["tax"] or ""),
								str(item.get("schedule_date") or self.required_by),
							)
						)
			if expected_lead_times is not None:
				self.assertEqual(actual_lead_times, expected_lead_times)

			adapter = SimpleNamespace(
				get_purchase_order_workspace=lambda purchase_order: {
					"record": {"name": purchase_order}
				}
			)
			result = save_purchase_order_document(
				{
					"naming_series": "YRP-PO-.YYYY.-",
					"supplier": supplier,
					"transaction_date": self.order_date,
					"schedule_date": self.required_by,
					"company": self.company,
					"currency": self.currency,
					"set_warehouse": self.warehouse,
					"item_details": groups,
				},
				name=name,
				expected_modified=expected_modified,
				_adapter=adapter,
			)
			doc = frappe.get_doc("Purchase Order", result["record"]["name"])
			actual = [
				(flt(row.rate), str(row.tax or ""), str(row.schedule_date)) for row in doc.items
			]
			self.assertEqual(actual, expected)
			return doc
		finally:
			frappe.db.set_single_value("YRP Settings", "enable_price_validation", original)

	def _save_reload(self, groups, supplier, *, enabled):
		original = frappe.db.get_single_value("YRP Settings", "enable_price_validation")
		frappe.db.set_single_value("YRP Settings", "enable_price_validation", enabled)
		try:
			adapter = SimpleNamespace(
				get_purchase_order_workspace=lambda purchase_order: {
					"record": {"name": purchase_order}
				}
			)
			result = save_purchase_order_document(
				{
					"naming_series": "YRP-PO-.YYYY.-",
					"supplier": supplier,
					"transaction_date": self.order_date,
					"schedule_date": self.required_by,
					"company": self.company,
					"currency": self.currency,
					"set_warehouse": self.warehouse,
					"item_details": groups,
				},
				_adapter=adapter,
			)
			return frappe.get_doc("Purchase Order", result["record"]["name"])
		finally:
			frappe.db.set_single_value("YRP Settings", "enable_price_validation", original)

	def _plain_groups(self, *, two_rows):
		items = [self._item_entry(self.plain_item, {"default": {"qty": 6, "rate": 999}})]
		if two_rows:
			items.append(self._item_entry(self.plain_item, {"default": {"qty": 6, "rate": 777}}))
		return [{"attributes": [], "primary_attribute": "", "primary_attribute_values": [], "items": items}]

	def _attribute_groups(self):
		return [{
			"attributes": [self.colour],
			"primary_attribute": self.size,
			"primary_attribute_values": ["S", "M"],
			"items": [
				self._item_entry(
					self.attribute_item,
					{"S": {"qty": 3, "rate": 999}, "M": {"qty": 0, "rate": 999}},
					attributes={self.colour: "Red"},
					primary_attribute=self.size,
					fetch_delivery_date=1,
					schedule_date=self.required_by,
				),
				self._item_entry(
					self.attribute_item,
					{"S": {"qty": 0, "rate": 999}, "M": {"qty": 4, "rate": 999}},
					attributes={self.colour: "Blue"},
					primary_attribute=self.size,
					fetch_delivery_date=0,
					schedule_date=add_days(self.order_date, 15),
				),
			],
		}]

	def _primary_attribute_groups(self):
		return [{
			"attributes": [],
			"primary_attribute": self.size,
			"primary_attribute_values": ["S", "M"],
			"items": [self._item_entry(
				self.primary_item,
				{"S": {"qty": 3, "rate": 999}, "M": {"qty": 12, "rate": 999}},
				primary_attribute=self.size,
				fetch_delivery_date=1,
				schedule_date=self.required_by,
			)],
		}]

	def _item_entry(
		self,
		item,
		values,
		*,
		attributes=None,
		primary_attribute="",
		fetch_delivery_date=0,
		schedule_date=None,
		tax="",
	):
		return {
			"name": item,
			"attributes": attributes or {},
			"primary_attribute": primary_attribute,
			"values": values,
			"default_uom": self.uom,
			"stock_uom": self.uom,
			"conversion_factor": 1,
			"delivery_location": "",
			"schedule_date": schedule_date or self.required_by,
			"expected_delivery_date": schedule_date or self.required_by,
			"fetch_delivery_date": fetch_delivery_date,
			"discount_percentage": 0,
			"tax": tax,
			"additional_parameters": [],
		}


class TestPurchaseOrderPriceValidationAdditional(TestCase):
	def test_attribute_preview_groups_each_attribute_value_separately(self):
		price = self.make_price(
			[
				{"moq": 0, "price": 100, "lead_time": 2, "attribute_value": "Red"},
				{"moq": 10, "price": 80, "lead_time": 5, "attribute_value": "Red"},
				{"moq": 0, "price": 120, "lead_time": 4, "attribute_value": "Blue"},
				{"moq": 10, "price": 90, "lead_time": 6, "attribute_value": "Blue"},
			],
			attribute="Colour",
		)
		rows = [
			frappe._dict(item_code="ITEM-RED", qty=6),
			frappe._dict(item_code="ITEM-BLUE", qty=6),
		]
		with patch(
			"yrp.yrp.doctype.yrp_item_price.yrp_item_price.get_active_price",
			return_value=price,
		):
			preview = purchase_order.apply_price_details(
				rows,
				"SUPPLIER-1",
				strict=False,
				preview_context=[
					{"parent_item": "ITEM", "attributes": {"Colour": "Red"}},
					{"parent_item": "ITEM", "attributes": {"Colour": "Blue"}},
				],
			)

		self.assertEqual([row.rate for row in rows], [100, 120])
		self.assertEqual([row["lead_time_days"] for row in preview["rows"]], [2, 4])

	def test_save_pricing_ignores_forged_preview_identity_fields(self):
		price = self.make_price(
			[
				{"moq": 0, "price": 110, "lead_time": 2, "attribute_value": "Red"},
				{"moq": 0, "price": 990, "lead_time": 9, "attribute_value": "Blue"},
			],
			attribute="Colour",
		)
		row = frappe._dict(
			item_code="REAL-RED-VARIANT",
			qty=4,
			_price_parent_item="FORGED-TEMPLATE",
			_price_attributes={"Colour": "Blue"},
		)
		with (
			patch.object(purchase_order, "_get_parent_item", return_value="REAL-TEMPLATE"),
			patch.object(purchase_order, "_get_variant_attribute_value", return_value="Red"),
			patch(
				"yrp.yrp.doctype.yrp_item_price.yrp_item_price.get_active_price",
				return_value=price,
			) as get_active_price,
		):
			purchase_order.apply_price_details([row], "SUPPLIER-1", strict=False)

		get_active_price.assert_called_once_with("REAL-TEMPLATE", "SUPPLIER-1", raise_error=False)
		self.assertEqual(row.rate, 110)

	def test_preview_identity_is_accepted_only_through_explicit_context(self):
		price = self.make_price(
			[{"moq": 0, "price": 125, "lead_time": 4, "attribute_value": "Red"}],
			attribute="Colour",
		)
		row = frappe._dict(item_code="TEMPLATE-WITHOUT-VARIANT", qty=3)
		with patch(
			"yrp.yrp.doctype.yrp_item_price.yrp_item_price.get_active_price",
			return_value=price,
		):
			preview = purchase_order.apply_price_details(
				[row],
				"SUPPLIER-1",
				strict=False,
				preview_context=[
					{"parent_item": "TEMPLATE-WITHOUT-VARIANT", "attributes": {"Colour": "Red"}}
				],
			)

		self.assertEqual(row.rate, 125)
		self.assertEqual(preview["rows"][0]["lead_time_days"], 4)

	def test_preview_reports_missing_ambiguous_and_no_slab(self):
		row = frappe._dict(item_code="ITEM", qty=4)
		context = [{"parent_item": "ITEM", "attributes": {}}]
		with (
			patch(
				"yrp.yrp.doctype.yrp_item_price.yrp_item_price.get_active_price",
				return_value=None,
			),
			patch.object(purchase_order, "_active_price_names", return_value=[]),
		):
			missing = purchase_order.apply_price_details(
				[row], "SUPPLIER-1", strict=False, preview_context=context
			)
		self.assertEqual(missing["rows"][0]["status"], "missing")

		with (
			patch(
				"yrp.yrp.doctype.yrp_item_price.yrp_item_price.get_active_price",
				return_value=None,
			),
			patch.object(purchase_order, "_active_price_names", return_value=["PRICE-1", "PRICE-2"]),
		):
			ambiguous = purchase_order.apply_price_details(
				[row], "SUPPLIER-1", strict=False, preview_context=context
			)
		self.assertEqual(ambiguous["rows"][0]["status"], "ambiguous")

		price = self.make_price([{"moq": 10, "price": 80, "lead_time": 7}])
		with patch(
			"yrp.yrp.doctype.yrp_item_price.yrp_item_price.get_active_price",
			return_value=price,
		):
			no_slab = purchase_order.apply_price_details(
				[row], "SUPPLIER-1", strict=False, preview_context=context
			)
		self.assertEqual(no_slab["rows"][0]["status"], "no_slab")

	@staticmethod
	def make_price(tiers, attribute=None):
		rows = [frappe._dict(tier) for tier in tiers]
		price = frappe._dict(
			depends_on_attribute=bool(attribute),
			attribute=attribute,
			tax="5",
			item_price_values=rows,
		)

		def validate_attribute_values(
			qty=0,
			attribute=None,
			attribute_value=None,
			get_lead_time=False,
		):
			if price.depends_on_attribute and (
				attribute != price.attribute or attribute_value is None
			):
				return None
			eligible = [
				row
				for row in rows
				if (row.attribute_value or None) == (attribute_value or None)
				and row.moq <= qty
			]
			if not eligible:
				return None
			selected = max(eligible, key=lambda row: row.moq)
			return selected.lead_time if get_lead_time else selected.price

		price.validate_attribute_values = validate_attribute_values
		return price

	def test_strict_submit_lookup_remains_authoritative(self):
		rows = [frappe._dict(item_variant="YARN-NAVY", qty=500)]

		with (
			patch.object(purchase_order, "_get_parent_item", return_value="60's GL Yarn"),
			patch(
				"yrp.yrp.doctype.yrp_item_price.yrp_item_price.get_active_price",
				side_effect=frappe.ValidationError("Active Item Price is required"),
			) as get_active_price,
			self.assertRaisesRegex(frappe.ValidationError, "Active Item Price is required"),
		):
			purchase_order.validate_price_details(
				rows,
				supplier="S-0010",
				strict=True,
			)

		get_active_price.assert_called_once_with("60's GL Yarn", "S-0010", raise_error=True)

	def test_submit_is_blocked_with_validation_enabled_and_setting_is_restored(self):
		class PurchaseOrderForSubmit(purchase_order.YRPPurchaseOrderMixin):
			is_yrp_managed = 1
			supplier = "SUPPLIER-WITHOUT-PRICE"
			items = [frappe._dict(item_code="ITEM-WITHOUT-PRICE", qty=1)]

			def get(self, fieldname):
				return getattr(self, fieldname, None)

			def set_item_defaults(self):
				pass

			def calculate_row_amount(self, row):
				pass

			def validate_items(self):
				pass

			def initialize_pending_quantities(self, force=False):
				pass

			def calculate_totals(self):
				pass

			def set_status(self):
				pass

		original = frappe.db.get_single_value("YRP Settings", "enable_price_validation")
		frappe.db.set_single_value("YRP Settings", "enable_price_validation", 1)
		try:
			with (
				patch.object(purchase_order, "_get_parent_item", return_value="ITEM-WITHOUT-PRICE"),
				self.assertRaisesRegex(frappe.ValidationError, "No Active Price List"),
			):
				PurchaseOrderForSubmit().before_submit()
		finally:
			frappe.db.set_single_value(
				"YRP Settings", "enable_price_validation", original
			)

		self.assertEqual(
			frappe.db.get_single_value("YRP Settings", "enable_price_validation"),
			original,
		)
