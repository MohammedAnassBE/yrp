import threading
import traceback
from unittest import TestCase
from unittest.mock import patch

import frappe
from erpnext.controllers.item_variant import create_variant as create_native_variant
from erpnext.controllers.item_variant import get_variant as get_native_variant

from yrp.concurrency_test_support import commit_connection
from yrp.stock.test_uom import _attribute, _ensure_uom, _india_compliance_item_fields, _item_group
from yrp.yrp.doctype.yrp_item.yrp_item import (
	create_variant,
	ensure_global_attribute_values,
	get_or_create_variant,
	get_parent_item,
)

NON_ITEM_MANAGER = "_test_variant_planner@example.com"


class TestGetOrCreateVariant(TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls.class_savepoint = "item_variant_class_" + frappe.generate_hash(length=8)
		frappe.db.savepoint(cls.class_savepoint)
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

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point=cls.class_savepoint)

	def setUp(self):
		self.savepoint = "item_variant_test_" + frappe.generate_hash(length=8)
		frappe.db.savepoint(self.savepoint)

	def tearDown(self):
		frappe.db.rollback(save_point=self.savepoint)
		frappe.set_user("Administrator")

	def test_parent_of_standalone_item_preserves_supplied_casing(self):
		supplied = self.plain_item.swapcase()
		self.assertNotEqual(supplied, self.plain_item)
		self.assertEqual(frappe.db.get_value("Item", supplied, "name"), self.plain_item)
		self.assertEqual(get_parent_item(supplied), supplied)

	def test_user_without_item_create_gets_new_variant_of_template(self):
		frappe.share.add("Item", self.template, NON_ITEM_MANAGER, read=1, notify=0)
		frappe.set_user(NON_ITEM_MANAGER)
		self.assertFalse(frappe.has_permission("Item", "create"))

		variant = get_or_create_variant(self.template, {self.attribute: self.values[0]})

		self.assertEqual(frappe.db.get_value("Item", variant, "variant_of"), self.template)

	def test_existing_variant_is_returned_without_insert(self):
		created = get_or_create_variant(self.template, {self.attribute: self.values[1]})
		frappe.set_user(NON_ITEM_MANAGER)

		with (
			patch("frappe.model.document.Document.insert") as insert,
			patch("yrp.yrp_retail.pricing.lock_pricing_policy") as lock_policy,
		):
			self.assertEqual(get_or_create_variant(self.template, {self.attribute: self.values[1]}), created)
		insert.assert_not_called()
		lock_policy.assert_not_called()

	def test_create_variant_existing_fast_lookup_skips_mutex(self):
		created = get_or_create_variant(self.template, {self.attribute: self.values[1]})

		with patch("yrp.yrp_retail.pricing.lock_pricing_policy") as lock_policy:
			variant = create_variant(self.template, {self.attribute: self.values[1]})

		self.assertEqual(variant.name, created)
		self.assertFalse(variant.is_new())
		lock_policy.assert_not_called()

	def test_existing_legacy_variant_does_not_backfill_blank_tuple_on_save(self):
		variant = get_or_create_variant(self.template, {self.attribute: self.values[1]})
		frappe.db.set_value("Item", variant, "item_tuple_attribute", None, update_modified=False)
		self.assertEqual(
			get_or_create_variant(self.template, {self.attribute: self.values[1]}), variant
		)
		self.assertIsNone(frappe.db.get_value("Item", variant, "item_tuple_attribute"))
		doc = frappe.get_doc("Item", variant)
		doc.description = "Legacy variant edit"

		doc.save(ignore_permissions=True)

		self.assertIsNone(frappe.db.get_value("Item", variant, "item_tuple_attribute"))

	def test_two_manufacturer_variants_do_not_share_an_empty_attribute_tuple(self):
		suffix = frappe.generate_hash(length=8)
		template = _item(
			f"_Test Manufacturer Template {suffix}",
			has_variants=1,
			variant_based_on="Manufacturer",
		)
		manufacturers = []
		for label in ("A", "B"):
			manufacturer = frappe.new_doc("Manufacturer")
			manufacturer.short_name = f"_Test Manufacturer {label} {suffix}"
			manufacturer.insert(ignore_permissions=True)
			manufacturers.append(manufacturer.name)

		variants = [
			get_native_variant(
				template,
				manufacturer=manufacturer,
				manufacturer_part_no=f"PART-{index}-{suffix}",
			).name
			for index, manufacturer in enumerate(manufacturers, start=1)
		]

		self.assertEqual(len(set(variants)), 2)
		self.assertEqual(
			frappe.get_all(
				"Item",
				filters={"name": ["in", variants]},
				pluck="item_tuple_attribute",
			),
			[None, None],
		)

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

	def test_native_item_save_blocks_attribute_changes_once_variants_exist(self):
		get_or_create_variant(self.template, {self.attribute: self.values[0]})
		template = frappe.get_doc("Item", self.template)
		second_attribute = f"_Test Locked Material {frappe.generate_hash(length=8)}"
		second_mapping = _attribute(second_attribute, f"Cotton {frappe.generate_hash(length=8)}")
		template.append("attributes", {"attribute": second_attribute, "mapping": second_mapping})

		with self.assertRaisesRegex(frappe.ValidationError, "variants exist"):
			template.save(ignore_permissions=True)

	def test_native_item_save_blocks_primary_attribute_change_once_variants_exist(self):
		get_or_create_variant(self.template, {self.attribute: self.values[0]})
		template = frappe.get_doc("Item", self.template)
		template.primary_attribute = self.attribute

		with self.assertRaisesRegex(frappe.ValidationError, "variants exist"):
			template.save(ignore_permissions=True)

	def test_native_item_save_blocks_mapping_swap_once_variants_exist(self):
		get_or_create_variant(self.template, {self.attribute: self.values[0]})
		template = frappe.get_doc("Item", self.template)
		replacement = frappe.get_doc(
			{
				"doctype": "YRP Item Item Attribute Mapping",
				"attribute_name": self.attribute,
				"values": [{"attribute_value": self.values[0]}],
			}
		).insert(ignore_permissions=True).name
		template.attributes[0].mapping = replacement

		with self.assertRaisesRegex(frappe.ValidationError, "variants exist"):
			template.save(ignore_permissions=True)

	def test_native_mapping_save_blocks_value_changes_once_variants_exist(self):
		get_or_create_variant(self.template, {self.attribute: self.values[0]})
		template = frappe.get_doc("Item", self.template)
		mapping = frappe.get_doc(
			"YRP Item Item Attribute Mapping", template.attributes[0].mapping
		)
		new_value = f"Green {frappe.generate_hash(length=6)}"
		ensure_global_attribute_values(self.attribute, [new_value], check_permission=False)
		mapping.append("values", {"attribute_value": new_value})

		with self.assertRaisesRegex(frappe.ValidationError, "variants exist"):
			mapping.save(ignore_permissions=True)

	def test_clearing_the_dependent_attribute_deletes_its_mapping(self):
		suffix = frappe.generate_hash(length=8)
		size = f"_Test Dependent Size {suffix}"
		stage = f"_Test Dependent Stage {suffix}"
		template = _item(
			f"_Test Dependent Template {suffix}",
			has_variants=1,
			primary_attribute=size,
			attributes=[
				{"attribute": size, "mapping": _attribute(size, "Small")},
				{"attribute": stage, "mapping": _attribute(stage, "Cut")},
			],
		)
		doc = frappe.get_doc("Item", template)
		doc.dependent_attribute = stage
		doc.save(ignore_permissions=True)
		mapping = doc.dependent_attribute_mapping
		self.assertTrue(frappe.db.exists("YRP Item Dependent Attribute Mapping", mapping))

		doc.dependent_attribute = None
		doc.save(ignore_permissions=True)

		self.assertIsNone(frappe.db.get_value("Item", template, "dependent_attribute_mapping"))
		self.assertFalse(frappe.db.exists("YRP Item Dependent Attribute Mapping", mapping))

	def test_native_item_and_mapping_attribute_changes_are_allowed_without_variants(self):
		suffix = frappe.generate_hash(length=8)
		first_attribute = f"_Test Editable Colour {suffix}"
		first_mapping = _attribute(first_attribute, f"Red {suffix}")
		template = _item(
			f"_Test Editable Template {suffix}",
			has_variants=1,
			attributes=[{"attribute": first_attribute, "mapping": first_mapping}],
		)
		second_attribute = f"_Test Editable Size {suffix}"
		second_mapping = _attribute(second_attribute, f"Small {suffix}")
		template_doc = frappe.get_doc("Item", template)
		template_doc.append(
			"attributes", {"attribute": second_attribute, "mapping": second_mapping}
		)
		template_doc.primary_attribute = second_attribute
		template_doc.save(ignore_permissions=True)

		stored_mapping = frappe.get_doc(
			"YRP Item Item Attribute Mapping", template_doc.attributes[0].mapping
		)
		new_value = f"Blue {suffix}"
		ensure_global_attribute_values(first_attribute, [new_value], check_permission=False)
		stored_mapping.append("values", {"attribute_value": new_value})
		stored_mapping.save(ignore_permissions=True)

		self.assertEqual(
			frappe.db.get_value("Item", template, "primary_attribute"), second_attribute
		)

	def test_unrestricted_numeric_attribute_allows_a_valid_variant_value(self):
		suffix = frappe.generate_hash(length=8)
		attribute = frappe.get_doc(
			{
				"doctype": "Item Attribute",
				"attribute_name": f"_Test Variant Gauge {suffix}",
				"numeric_values": 1,
				"from_range": 0,
				"to_range": 10,
				"increment": 0.5,
			}
		).insert(ignore_permissions=True)
		mapping = frappe.get_doc(
			{
				"doctype": "YRP Item Item Attribute Mapping",
				"attribute_name": attribute.name,
			}
		).insert(ignore_permissions=True)
		template = _item(
			f"_Test Numeric Variant Template {suffix}",
			has_variants=1,
			attributes=[
				{
					"attribute": attribute.name,
					"mapping": mapping.name,
					"numeric_values": 1,
					"from_range": 0,
					"to_range": 10,
					"increment": 0.5,
				}
			],
		)

		variant = get_or_create_variant(template, {attribute.name: "1.5"})

		self.assertEqual(frappe.db.get_value("Item", variant, "variant_of"), template)


class TestConcurrentItemVariants(TestCase):
	def setUp(self):
		self.run_token = frappe.generate_hash(length=12)
		self.addCleanup(self._cleanup_run_token)

	def _run_connection(self, callback):
		site = frappe.local.site
		sites_path = frappe.local.sites_path
		result = {}

		def run():
			frappe.init(site, sites_path=sites_path)
			frappe.connect()
			frappe.flags.in_test = True
			frappe.set_user("Administrator")
			try:
				result["value"] = callback()
			except Exception as error:
				frappe.db.rollback()
				result["error"] = f"{error}\n{traceback.format_exc()}"
			finally:
				frappe.destroy()

		thread = threading.Thread(target=run, daemon=True)
		thread.start()
		thread.join(timeout=20)
		self.assertFalse(thread.is_alive(), "Database helper connection timed out")
		self.assertNotIn("error", result, result.get("error"))
		return result.get("value")

	def _make_fixture(self):
		def create():
			suffix = f"{self.run_token}-{frappe.generate_hash(length=6)}"
			uom = frappe.db.get_value("UOM", {"name": ["!=", "Piece"]}, "name")
			attribute = f"_Test Variant Race Colour {suffix}"
			values = [f"Red {suffix}", f"Blue {suffix}"]
			mapping = _attribute(attribute, values[0])
			ensure_global_attribute_values(attribute, values, check_permission=False)
			mapping_doc = frappe.get_doc("YRP Item Item Attribute Mapping", mapping)
			mapping_doc.append("values", {"attribute_value": values[1]})
			mapping_doc.save(ignore_permissions=True)
			second_attribute = f"_Test Variant Race Size {suffix}"
			second_values = [f"Large {suffix}", f"Small {suffix}"]
			second_mapping = _attribute(second_attribute, second_values[0])
			ensure_global_attribute_values(second_attribute, second_values, check_permission=False)
			second_mapping_doc = frappe.get_doc(
				"YRP Item Item Attribute Mapping", second_mapping
			)
			second_mapping_doc.append("values", {"attribute_value": second_values[1]})
			second_mapping_doc.save(ignore_permissions=True)
			template = frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": f"_Test Variant Race Template {suffix}",
					"item_name": f"_Test Variant Race Template {suffix}",
					"item_group": _item_group(),
					"stock_uom": uom,
					"is_stock_item": 1,
					"has_variants": 1,
					"attributes": [{"attribute": attribute, "mapping": mapping}],
					**_india_compliance_item_fields(),
				}
			).insert(ignore_permissions=True).name
			template_doc = frappe.get_doc("Item", template)
			fixture = {
				"template": template,
				"uom": uom,
				"attribute": attribute,
				"values": values,
				"mapping": template_doc.attributes[0].mapping,
				"second_attribute": second_attribute,
				"second_mapping": second_mapping,
				"second_values": second_values,
				"attributes": [attribute, second_attribute],
			}
			commit_connection()
			return fixture

		return self._run_connection(create)

	def _cleanup_run_token(self):
		def token_records(doctype, *fieldnames):
			return frappe.get_all(
				doctype,
				or_filters={
					fieldname: ["like", f"%{self.run_token}%"]
					for fieldname in fieldnames
				},
				pluck="name",
				limit=0,
			)

		def cleanup():
			items = token_records("Item", "name", "item_name", "variant_of")
			variants = (
				frappe.get_all(
					"Item", filters={"variant_of": ["in", items]}, pluck="name", limit=0
				)
				if items
				else []
			)
			for name in [*variants, *items]:
				if frappe.db.exists("Item", name):
					frappe.delete_doc(
						"Item", name, force=True, ignore_permissions=True, delete_permanently=True
					)
			attribute_values = token_records(
				"Item Attribute Value", "name", "parent", "attribute_value"
			)
			if attribute_values:
				frappe.db.delete("Item Attribute Value", {"name": ["in", attribute_values]})
			for doctype, fieldnames in (
				("YRP Item Item Attribute Mapping", ("name", "attribute_name")),
				(
					"YRP Item Attribute Value",
					("name", "attribute_name", "attribute_value"),
				),
				("Item Attribute", ("name", "attribute_name")),
			):
				for name in token_records(doctype, *fieldnames):
					if frappe.db.exists(doctype, name):
						frappe.delete_doc(
							doctype,
							name,
							force=True,
							ignore_permissions=True,
							delete_permanently=True,
						)
			commit_connection()
			return {
				"Item": token_records("Item", "name", "item_name", "variant_of"),
				"Item Attribute": token_records(
					"Item Attribute", "name", "attribute_name"
				),
				"Item Attribute Value": token_records(
					"Item Attribute Value", "name", "parent", "attribute_value"
				),
				"Mapping": token_records(
					"YRP Item Item Attribute Mapping", "name", "attribute_name"
				),
				"IAV": token_records(
					"YRP Item Attribute Value", "name", "attribute_name", "attribute_value"
				),
			}

		leftovers = self._run_connection(cleanup)
		self.assertFalse(
			{doctype: names for doctype, names in leftovers.items() if names},
			f"Committed Item fixture leftovers for run token {self.run_token}",
		)

	def _cleanup_fixture(self, fixture):
		def cleanup():
			for variant in frappe.get_all(
				"Item", filters={"variant_of": fixture["template"]}, pluck="name"
			):
				frappe.delete_doc(
					"Item", variant, force=True, ignore_permissions=True, delete_permanently=True
				)
			if frappe.db.exists("Item", fixture["template"]):
				frappe.delete_doc(
					"Item",
					fixture["template"],
					force=True,
					ignore_permissions=True,
					delete_permanently=True,
				)
			for attribute in fixture["attributes"]:
				frappe.db.delete("Item Attribute Value", {"parent": attribute})
				for mapping in frappe.get_all(
					"YRP Item Item Attribute Mapping",
					filters={"attribute_name": attribute},
					pluck="name",
				):
					frappe.delete_doc(
						"YRP Item Item Attribute Mapping",
						mapping,
						force=True,
						ignore_permissions=True,
						delete_permanently=True,
					)
				for value in frappe.get_all(
					"YRP Item Attribute Value",
					filters={"attribute_name": attribute},
					pluck="name",
				):
					frappe.delete_doc(
						"YRP Item Attribute Value",
						value,
						force=True,
						ignore_permissions=True,
						delete_permanently=True,
					)
				if frappe.db.exists("Item Attribute", attribute):
					frappe.delete_doc(
						"Item Attribute",
						attribute,
						force=True,
						ignore_permissions=True,
						delete_permanently=True,
					)
			commit_connection()

		self._run_connection(cleanup)

	def _run_workers(self, workers, *, progress=None):
		site = frappe.local.site
		sites_path = frappe.local.sites_path
		results = {}
		result_lock = threading.Lock()

		def run(label, callback):
			frappe.init(site, sites_path=sites_path)
			frappe.connect()
			frappe.flags.in_test = True
			frappe.set_user("Administrator")
			if progress is not None:
				progress.operation = label
			try:
				value = callback()
				outcome = ("ok", value)
			except Exception as error:
				frappe.db.rollback()
				outcome = (type(error).__name__, f"{error}\n{traceback.format_exc()}")
			finally:
				frappe.destroy()
			with result_lock:
				results[label] = outcome

		threads = {
			label: threading.Thread(target=run, args=(label, callback), daemon=True)
			for label, callback in workers.items()
		}
		for thread in threads.values():
			thread.start()
		return threads, results

	def test_policy_lock_restores_snapshot_isolation_at_transaction_end(self):
		from yrp.yrp_retail.pricing import lock_pricing_policy

		def check_settings():
			for initial in ("ON", "OFF"):
				for finish in (commit_connection, frappe.db.rollback):
					frappe.db.sql(f"SET SESSION innodb_snapshot_isolation = {initial}")
					lock_pricing_policy()
					lock_pricing_policy()
					self.assertEqual(frappe.db.sql("SELECT @@innodb_snapshot_isolation")[0][0], 0)
					finish()
					self.assertEqual(
						frappe.db.sql("SELECT @@innodb_snapshot_isolation")[0][0], int(initial == "ON")
					)

		if frappe.db.db_type != "mariadb":
			self.skipTest("MariaDB snapshot isolation")
		if not frappe.db.sql("SHOW SESSION VARIABLES LIKE 'innodb_snapshot_isolation'"):
			self.skipTest("MariaDB predates snapshot isolation")
		self._run_connection(check_settings)

	def test_get_or_create_sees_native_variant_committed_after_its_snapshot(self):
		fixture = self._make_fixture()
		snapshot_ready = threading.Event()
		native_committed = threading.Event()

		def get_or_create_from_old_snapshot():
			frappe.get_all(
				"Item", filters={"variant_of": fixture["template"]}, pluck="name"
			)
			snapshot_ready.set()
			if not native_committed.wait(10):
				raise TimeoutError("Native variant did not commit")
			name = get_or_create_variant(
				fixture["template"], {fixture["attribute"]: fixture["values"][0]}
			)
			commit_connection()
			return name

		def create_native():
			if not snapshot_ready.wait(10):
				raise TimeoutError("YRP creator did not establish its snapshot")
			variant = create_native_variant(
				fixture["template"], {fixture["attribute"]: fixture["values"][0]}
			)
			variant.insert(ignore_permissions=True)
			commit_connection()
			native_committed.set()
			return variant.name

		threads, results = self._run_workers(
			{"yrp": get_or_create_from_old_snapshot, "native": create_native}
		)
		try:
			for thread in threads.values():
				thread.join(timeout=20)
			self.assertTrue(all(not thread.is_alive() for thread in threads.values()), results)
			self.assertEqual(results.get("native", (None,))[0], "ok", results)
			self.assertEqual(results.get("yrp", (None,))[0], "ok", results)
			self.assertEqual(results["yrp"][1], results["native"][1])
			self.assertEqual(
				self._run_connection(
					lambda: frappe.db.count("Item", {"variant_of": fixture["template"]})
				),
				1,
			)
		finally:
			native_committed.set()
			self._cleanup_fixture(fixture)

	def test_variant_creation_and_mapping_removal_follow_mutex_then_template_order(self):
		fixture = self._make_fixture()
		mapping_mutex_acquired = threading.Event()
		variant_mutex_requested = threading.Event()
		release_mapping = threading.Event()
		progress = threading.local()
		database_class = type(frappe.local.db)
		original_sql = database_class.sql

		def observe_sql(database, query, *args, **kwargs):
			lowered = query.lower() if isinstance(query, str) else ""
			is_mutex = (
				"tabdoctype" in lowered
				and "yrp item master template" in lowered
				and "for update" in lowered
			)
			operation = getattr(progress, "operation", None)
			if operation == "variant" and is_mutex:
				variant_mutex_requested.set()
			result = original_sql(database, query, *args, **kwargs)
			if operation == "mapping" and is_mutex and not mapping_mutex_acquired.is_set():
				mapping_mutex_acquired.set()
				if not release_mapping.wait(10):
					raise TimeoutError("Variant creation did not request the pricing mutex")
			return result

		def remove_mapping_value():
			mapping = frappe.get_doc("YRP Item Item Attribute Mapping", fixture["mapping"])
			mapping.set("values", [mapping.values[0]])
			mapping.save(ignore_permissions=True)
			commit_connection()

		def create_variant():
			if not mapping_mutex_acquired.wait(10):
				raise TimeoutError("Mapping removal did not acquire the pricing mutex")
			name = get_or_create_variant(
				fixture["template"], {fixture["attribute"]: fixture["values"][1]}
			)
			commit_connection()
			return name

		try:
			with patch.object(database_class, "sql", new=observe_sql):
				threads, results = self._run_workers(
					{"mapping": remove_mapping_value, "variant": create_variant},
					progress=progress,
				)
				self.assertTrue(variant_mutex_requested.wait(10), results)
				release_mapping.set()
				for thread in threads.values():
					thread.join(timeout=20)
			self.assertTrue(all(not thread.is_alive() for thread in threads.values()), results)
			self.assertEqual(results.get("mapping", (None,))[0], "ok", results)
			self.assertEqual(results.get("variant", (None,))[0], "ValidationError", results)
			self.assertIn("not allowed", results["variant"][1])
		finally:
			release_mapping.set()
			self._cleanup_fixture(fixture)

	def test_variant_creation_and_template_edit_follow_mutex_then_template_order(self):
		fixture = self._make_fixture()
		variant_mutex_acquired = threading.Event()
		item_mutex_requested = threading.Event()
		release_variant = threading.Event()
		progress = threading.local()
		database_class = type(frappe.local.db)
		original_sql = database_class.sql

		def observe_sql(database, query, *args, **kwargs):
			lowered = query.lower() if isinstance(query, str) else ""
			is_mutex = (
				"tabdoctype" in lowered
				and "yrp item master template" in lowered
				and "for update" in lowered
			)
			operation = getattr(progress, "operation", None)
			if operation == "edit" and is_mutex:
				item_mutex_requested.set()
			result = original_sql(database, query, *args, **kwargs)
			if operation == "variant" and is_mutex and not variant_mutex_acquired.is_set():
				variant_mutex_acquired.set()
				if not release_variant.wait(10):
					raise TimeoutError("Template edit did not request the pricing mutex")
			return result

		def create_variant():
			name = get_or_create_variant(
				fixture["template"], {fixture["attribute"]: fixture["values"][0]}
			)
			commit_connection()
			return name

		def edit_template():
			if not variant_mutex_acquired.wait(10):
				raise TimeoutError("Variant creator did not acquire the pricing mutex")
			template = frappe.get_doc("Item", fixture["template"])
			template.append(
				"attributes",
				{
					"attribute": fixture["second_attribute"],
					"mapping": fixture["second_mapping"],
				},
			)
			template.save(ignore_permissions=True)
			commit_connection()

		try:
			with patch.object(database_class, "sql", new=observe_sql):
				threads, results = self._run_workers(
					{"variant": create_variant, "edit": edit_template},
					progress=progress,
				)
				self.assertTrue(item_mutex_requested.wait(10), results)
				release_variant.set()
				for thread in threads.values():
					thread.join(timeout=20)
			self.assertTrue(all(not thread.is_alive() for thread in threads.values()), results)
			self.assertEqual(results.get("variant", (None,))[0], "ok", results)
			self.assertEqual(results.get("edit", (None,))[0], "ValidationError", results)
			self.assertIn("variants exist", results["edit"][1])
		finally:
			release_variant.set()
			self._cleanup_fixture(fixture)

	def test_item_editor_first_interleaving_finishes_before_variant_rechecks_mapping(self):
		from essdee_yrp.purchase.item_editor import update_item

		fixture = self._make_fixture()
		editor_mutex_acquired = threading.Event()
		variant_mutex_requested = threading.Event()
		progress = threading.local()
		database_class = type(frappe.local.db)
		original_sql = database_class.sql

		def observe_sql(database, query, *args, **kwargs):
			lowered = query.lower() if isinstance(query, str) else ""
			is_mutex = (
				"tabdoctype" in lowered
				and "yrp item master template" in lowered
				and "for update" in lowered
			)
			operation = getattr(progress, "operation", None)
			if operation == "variant" and is_mutex:
				variant_mutex_requested.set()
			result = original_sql(database, query, *args, **kwargs)
			if operation == "editor" and is_mutex and not editor_mutex_acquired.is_set():
				editor_mutex_acquired.set()
				if not variant_mutex_requested.wait(10):
					raise TimeoutError("Variant creation did not request the pricing mutex")
			return result

		def edit_template():
			template = frappe.get_doc("Item", fixture["template"])
			mapping = frappe.get_doc(
				"YRP Item Item Attribute Mapping", fixture["mapping"]
			)
			update_item(
				template.name,
				{
					"name": template.item_name,
					"brand": template.brand,
					"item_group": template.item_group,
					"stock_uom": template.stock_uom,
					"gst_hsn_code": template.get("gst_hsn_code"),
					"description": template.description,
					"disabled": template.disabled,
					"primary_attribute": template.primary_attribute,
					"attributes": [
						{
							"attribute": fixture["attribute"],
							"mapping_modified": mapping.modified,
							"values": [fixture["values"][0]],
						}
					],
				},
				template.modified,
			)
			commit_connection()

		def create_removed_variant():
			if not editor_mutex_acquired.wait(10):
				raise TimeoutError("Item editor did not acquire the pricing mutex")
			name = get_or_create_variant(
				fixture["template"], {fixture["attribute"]: fixture["values"][1]}
			)
			commit_connection()
			return name

		try:
			with patch.object(database_class, "sql", new=observe_sql):
				threads, results = self._run_workers(
					{"editor": edit_template, "variant": create_removed_variant},
					progress=progress,
				)
				for thread in threads.values():
					thread.join(timeout=20)
			self.assertTrue(all(not thread.is_alive() for thread in threads.values()), results)
			self.assertEqual(results.get("editor", (None,))[0], "ok", results)
			self.assertEqual(results.get("variant", (None,))[0], "ValidationError", results)
			self.assertIn("not allowed", results["variant"][1])
		finally:
			self._cleanup_fixture(fixture)


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
