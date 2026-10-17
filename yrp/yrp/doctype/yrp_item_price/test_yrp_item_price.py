import threading
import traceback
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.utils import add_days, nowdate

from yrp.concurrency_test_support import commit_connection
from yrp.stock.test_uom import _attribute, _ensure_uom, _india_compliance_item_fields, _item_group
from yrp.yrp.doctype.yrp_item.yrp_item import ensure_global_attribute_values
from yrp.yrp.doctype.yrp_item_price.yrp_item_price import (
	get_active_price,
	update_all_expired_item_price,
)


def _fixture_names(doctype, fieldname, token):
	return frappe.get_all(
		doctype,
		filters={fieldname: ["like", f"%{token}%"]},
		pluck="name",
		limit=0,
	)


def _committed_fixture_leftovers(token):
	return {
		"Item": _fixture_names("Item", "name", token),
		"UOM": _fixture_names("UOM", "name", token),
		"Supplier": _fixture_names("Supplier", "name", token),
		"User": _fixture_names("User", "name", token),
		"Brand": _fixture_names("Brand", "name", token),
		"Item Group": _fixture_names("Item Group", "name", token),
		"Item Attribute": _fixture_names("Item Attribute", "name", token),
		"YRP Item Item Attribute Mapping": _fixture_names(
			"YRP Item Item Attribute Mapping", "attribute_name", token
		),
		"YRP Item Attribute Value": _fixture_names("YRP Item Attribute Value", "attribute_name", token),
		"YRP Item Price": _fixture_names("YRP Item Price", "item_name", token),
	}


def _cleanup_committed_fixtures(token):
	items = _fixture_names("Item", "name", token)
	suppliers = _fixture_names("Supplier", "name", token)
	prices = set(_fixture_names("YRP Item Price", "item_name", token))
	if suppliers:
		prices.update(
			frappe.get_all(
				"YRP Item Price",
				filters={"supplier": ["in", suppliers]},
				pluck="name",
				limit=0,
			)
		)
	for name in prices:
		if not frappe.db.exists("YRP Item Price", name):
			continue
		frappe.db.set_value(
			"YRP Item Price",
			name,
			{"docstatus": 0, "workflow_state": "Draft"},
			update_modified=False,
		)
		frappe.delete_doc(
			"YRP Item Price", name, force=True, ignore_permissions=True, delete_permanently=True
		)

	variants = (
		frappe.get_all("Item", filters={"variant_of": ["in", items]}, pluck="name", limit=0) if items else []
	)
	for name in [*variants, *items]:
		if frappe.db.exists("Item", name):
			frappe.delete_doc("Item", name, force=True, ignore_permissions=True, delete_permanently=True)

	attributes = _fixture_names("Item Attribute", "name", token)
	for attribute in attributes:
		frappe.db.delete("Item Attribute Value", {"parent": attribute})
	for doctype, fieldname in (
		("YRP Item Item Attribute Mapping", "attribute_name"),
		("YRP Item Attribute Value", "attribute_name"),
		("Item Attribute", "name"),
	):
		for name in _fixture_names(doctype, fieldname, token):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True, delete_permanently=True)

	for doctype in ("Supplier", "UOM", "User", "Brand", "Item Group"):
		for name in _fixture_names(doctype, "name", token):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True, delete_permanently=True)
	commit_connection()
	return {doctype: names for doctype, names in _committed_fixture_leftovers(token).items() if names}


def _cleanup_fixture_run(site, sites_path, token):
	result = {}

	def cleanup():
		frappe.init(site, sites_path=sites_path)
		frappe.connect()
		frappe.flags.in_test = True
		frappe.set_user("Administrator")
		try:
			result["leftovers"] = _cleanup_committed_fixtures(token)
		except Exception as error:
			frappe.db.rollback()
			result["error"] = f"{error}\n{traceback.format_exc()}"
		finally:
			frappe.destroy()

	thread = threading.Thread(target=cleanup, daemon=True)
	thread.start()
	thread.join(timeout=30)
	if thread.is_alive():
		raise AssertionError(f"Fixture cleanup timed out for run token {token}")
	if result.get("error"):
		raise AssertionError(result["error"])
	if result.get("leftovers"):
		raise AssertionError(f"Committed fixture leftovers for run token {token}: {result['leftovers']}")


class TestItemPrice(TestCase):
	def test_non_attribute_price_matches_blank_child_attribute(self):
		doc = frappe.get_doc({"doctype": "YRP Item Price"})

		rate = doc.get_price_value(
			[[0, 5, 3, ""]],
			qty=5,
			attribute_value=None,
		)

		self.assertEqual(rate, 5)

	def test_non_attribute_lead_time_matches_blank_child_attribute(self):
		doc = frappe.get_doc({"doctype": "YRP Item Price"})

		lead_time = doc.get_price_value(
			[[0, 5, 3, ""]],
			qty=5,
			attribute_value=None,
			get_lead_time=True,
		)

		self.assertEqual(lead_time, 3)

	def test_empty_supplier_stops_before_querying(self):
		with patch.object(frappe.db, "get_list", return_value=[]) as get_list:
			self.assertIsNone(get_active_price("ITEM", None, raise_error=False))
			self.assertIsNone(get_active_price("ITEM", "", raise_error=False))
			with self.assertRaisesRegex(frappe.ValidationError, "No Active Price List"):
				get_active_price("ITEM", None)

		get_list.assert_not_called()

	def test_exact_supplier_lookup_never_retries_without_supplier(self):
		with (
			patch.object(frappe.db, "get_list", return_value=[]) as get_list,
			patch(
				"yrp.yrp.doctype.yrp_item_price.yrp_item_price.get_active_workflow_state_field",
				return_value=None,
			),
		):
			self.assertIsNone(get_active_price("ITEM", "SUPPLIER-A", raise_error=False))

		self.assertEqual(get_list.call_count, 2)
		for call in get_list.call_args_list:
			self.assertEqual(call.kwargs["filters"]["supplier"], "SUPPLIER-A")

	def test_expiry_commits_each_price_and_each_error_log_without_stopping(self):
		deadlocked = Mock()
		deadlocked.cancel.side_effect = frappe.QueryDeadlockError("forced deadlock")
		failed = Mock()
		failed.cancel.side_effect = frappe.ValidationError("forced validation failure")
		succeeded = Mock()

		with (
			patch.object(frappe.db, "get_all", return_value=["PRICE-A", "PRICE-B", "PRICE-C"]),
			patch.object(frappe, "get_doc", side_effect=[deadlocked, failed, succeeded]),
			patch.object(frappe.db, "commit") as commit,
			patch.object(frappe.db, "rollback") as rollback,
			patch.object(frappe, "log_error") as log_error,
			patch(
				"yrp.yrp.doctype.yrp_item_price.yrp_item_price.get_active_workflow_state_field",
				return_value=None,
			),
		):
			update_all_expired_item_price()

		self.assertEqual(rollback.call_count, 2)
		self.assertEqual(commit.call_count, 3)
		succeeded.cancel.assert_called_once_with()
		succeeded.add_comment.assert_called_once_with("Info", "Cancelled automatically due to expiry")
		self.assertEqual(log_error.call_count, 2)
		self.assertIn("PRICE-A", str(log_error.call_args_list[0]))
		self.assertIn("PRICE-B", str(log_error.call_args_list[1]))


class TestItemPriceAttributeValueQuery(TestCase):
	def test_link_validation_finds_the_stored_attribute_value_name(self):
		from yrp.attribute_value_identity import attribute_value_name
		from yrp.yrp.doctype.yrp_item import yrp_item

		filters = {"attribute": "_Test Price Size"}
		stored = attribute_value_name("_Test Price Size", "28")

		def search(txt):
			return yrp_item.get_item_attribute_values("YRP Item Attribute Value", txt, "name", 0, 10, filters)

		with patch.object(yrp_item, "get_global_attribute_values", return_value=["28", "30"]):
			self.assertEqual(search(stored), [[stored, "28"]])
			self.assertEqual(search("3"), [[attribute_value_name("_Test Price Size", "30"), "30"]])

	def test_link_validation_filters_hash_names_before_pagination(self):
		from yrp.attribute_value_identity import attribute_value_name
		from yrp.yrp.doctype.yrp_item import yrp_item

		attribute = "_Test Price Size"
		stored = attribute_value_name(attribute, "28")
		with patch.object(yrp_item, "get_global_attribute_values", return_value=["30", "28"]):
			self.assertEqual(
				yrp_item.get_item_attribute_values(
					"YRP Item Attribute Value", stored, "name", 0, 1, {"attribute": attribute}
				),
				[[stored, "28"]],
			)


class TestItemPriceNativePaths(TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls.class_savepoint = "item_price_class_" + frappe.generate_hash(length=8)
		frappe.db.savepoint(cls.class_savepoint)
		cls.class_savepoint_active = True
		cls.suffix = frappe.generate_hash(length=8)
		cls.cleanup_site = frappe.local.site
		cls.cleanup_sites_path = frappe.local.sites_path
		cls.addClassCleanup(cls._cleanup_class_fixtures)
		cls.stock_uom = _ensure_uom(f"Price Unit {cls.suffix}")
		cls.other_uom = _ensure_uom(f"Wrong Price Unit {cls.suffix}")
		cls.item = cls._make_item(f"_Test Price Item {cls.suffix}")
		cls.suppliers = [cls._make_supplier(label) for label in ("A", "B", "C")]
		cls.tax = cls._make_tax()
		cls.purchase_user = cls._make_user("purchase-user", ["Purchase User"])
		cls.purchase_manager = cls._make_user("purchase-manager", ["Purchase User", "Purchase Manager"])

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._rollback_class_savepoint()

	@classmethod
	def _rollback_class_savepoint(cls):
		if cls.class_savepoint_active:
			frappe.db.rollback(save_point=cls.class_savepoint)
			cls.class_savepoint_active = False

	@classmethod
	def _cleanup_class_fixtures(cls):
		cls._rollback_class_savepoint()
		_cleanup_fixture_run(cls.cleanup_site, cls.cleanup_sites_path, cls.suffix)

	@classmethod
	def _make_item(cls, name):
		return (
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": name,
					"item_name": name,
					"item_group": _item_group(),
					"stock_uom": cls.stock_uom,
					"is_stock_item": 1,
					"is_purchase_item": 1,
					**_india_compliance_item_fields(),
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	@classmethod
	def _make_supplier(cls, label):
		name = f"_Test Price Supplier {label} {cls.suffix}"
		return (
			frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": name,
					"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	@classmethod
	def _make_tax(cls):
		if existing := frappe.db.get_value("YRP Tax Slab", {}, "name"):
			return existing
		percentage = f"0.{frappe.utils.now_datetime().strftime('%f')}"
		return (
			frappe.get_doc({"doctype": "YRP Tax Slab", "percentage": percentage, "enabled": 1})
			.insert(ignore_permissions=True)
			.name
		)

	@classmethod
	def _make_user(cls, label, roles):
		email = f"{label}-{cls.suffix}@example.test"
		return (
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": label,
					"enabled": 1,
					"user_type": "System User",
					"send_welcome_email": 0,
					"roles": [{"role": role} for role in roles],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def setUp(self):
		self.previous_user = frappe.session.user
		frappe.set_user("Administrator")
		self.savepoint = "item_price_test_" + frappe.generate_hash(length=8)
		frappe.db.savepoint(self.savepoint)

	def tearDown(self):
		frappe.db.rollback(save_point=self.savepoint)
		frappe.set_user(self.previous_user)

	def make_price(self, *, supplier=None, from_date=None, to_date=None, uom=None, tiers=None):
		if tiers is None:
			tiers = [{"moq": 0, "price": 10, "lead_time": 2}]
		return frappe.get_doc(
			{
				"doctype": "YRP Item Price",
				"item_name": self.item,
				"supplier": self.suppliers[0] if supplier is None else supplier,
				"uom": self.stock_uom if uom is None else uom,
				"from_date": from_date or nowdate(),
				"to_date": to_date,
				"tax": self.tax,
				"item_price_values": tiers,
			}
		)

	def approve(self, **kwargs):
		doc = self.make_price(**kwargs).insert(ignore_permissions=True)
		doc.submit()
		return doc

	def test_native_insert_rejects_blank_supplier(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Supplier is required"):
			self.make_price(supplier="").insert(ignore_permissions=True)

	def test_native_insert_rejects_wrong_uom_instead_of_relabelling_it(self):
		with self.assertRaisesRegex(frappe.ValidationError, "stock UOM"):
			self.make_price(uom=self.other_uom).insert(ignore_permissions=True)

	def test_native_insert_enforces_tier_and_date_invariants(self):
		invalid = (
			({"tiers": []}, "price tier"),
			({"tiers": [{"moq": -1, "price": 10, "lead_time": 1}]}, "MOQ"),
			({"tiers": [{"moq": 0, "price": 0, "lead_time": 1}]}, "Price"),
			({"tiers": [{"moq": 0, "price": 10, "lead_time": -1}]}, "Lead Time"),
			(
				{
					"tiers": [
						{"moq": 0, "price": 10, "lead_time": 1, "attribute_value": "Red"},
						{"moq": 0, "price": 11, "lead_time": 1, "attribute_value": "Blue"},
					]
				},
				"Duplicate entries",
			),
			({"from_date": nowdate(), "to_date": add_days(nowdate(), -1)}, "To Date"),
		)
		for values, message in invalid:
			with self.subTest(values=values), self.assertRaisesRegex(frappe.ValidationError, message):
				self.make_price(**values).insert(ignore_permissions=True)

	def test_attribute_price_rejects_value_outside_item_mapping(self):
		suffix = frappe.generate_hash(length=8)
		attribute = f"_Test Price Colour {suffix}"
		mapping = _attribute(attribute, "Red")
		ensure_global_attribute_values(attribute, ["Blue"], check_permission=False)
		item = self._make_item(f"_Test Attribute Price Item {suffix}")
		item_doc = frappe.get_doc("Item", item)
		item_doc.has_variants = 1
		item_doc.append("attributes", {"attribute": attribute, "mapping": mapping})
		item_doc.save(ignore_permissions=True)
		doc = self.make_price(tiers=[{"moq": 0, "price": 10, "lead_time": 1, "attribute_value": "Blue"}])
		doc.item_name = item
		doc.depends_on_attribute = 1
		doc.attribute = attribute

		with self.assertRaisesRegex(frappe.ValidationError, "not allowed for this Item"):
			doc.insert(ignore_permissions=True)

	def test_attribute_price_rejects_same_label_from_another_attribute(self):
		suffix = frappe.generate_hash(length=8)
		colour = f"_Test Scoped Colour {suffix}"
		finish = f"_Test Scoped Finish {suffix}"
		colour_mapping = _attribute(colour, "Red")
		finish_mapping = _attribute(finish, "Red")
		item = self._make_item(f"_Test Scoped Attribute Price Item {suffix}")
		item_doc = frappe.get_doc("Item", item)
		item_doc.has_variants = 1
		item_doc.extend(
			"attributes",
			[
				{"attribute": colour, "mapping": colour_mapping},
				{"attribute": finish, "mapping": finish_mapping},
			],
		)
		item_doc.save(ignore_permissions=True)
		doc = self.make_price(tiers=[{"moq": 0, "price": 10, "lead_time": 1, "attribute_value": "Red"}])
		doc.item_name = item
		doc.depends_on_attribute = 1
		doc.attribute = colour
		doc.insert(ignore_permissions=True)
		doc.attribute = finish

		with self.assertRaisesRegex(frappe.ValidationError, "not allowed for this Item"):
			doc.save(ignore_permissions=True)

	def test_active_price_is_exact_supplier_only(self):
		self.approve(supplier=self.suppliers[0])
		self.assertEqual(
			get_active_price(self.item, self.suppliers[0], raise_error=False).supplier,
			self.suppliers[0],
		)
		self.assertIsNone(get_active_price(self.item, self.suppliers[1], raise_error=False))

	def test_multiple_active_exact_supplier_prices_return_none(self):
		first = self.approve(supplier=self.suppliers[0])
		second = self.make_price(supplier=self.suppliers[0]).insert(ignore_permissions=True)
		frappe.db.set_value(
			"YRP Item Price",
			second.name,
			{"docstatus": 1, "workflow_state": "Approved"},
			update_modified=False,
		)

		self.assertIsNone(get_active_price(self.item, self.suppliers[0], raise_error=False))
		self.assertEqual(first.docstatus, 1)

	def test_approval_closes_only_the_overlapping_predecessor(self):
		oldest = self.approve(from_date=add_days(nowdate(), -30))
		middle = self.approve(from_date=add_days(nowdate(), -20))
		oldest.reload()
		original_oldest_to_date = oldest.to_date

		newest = self.approve(from_date=add_days(nowdate(), -10))

		oldest.reload()
		middle.reload()
		self.assertEqual(oldest.to_date, original_oldest_to_date)
		self.assertEqual(middle.to_date, frappe.utils.getdate(add_days(newest.from_date, -1)))

	def test_same_start_date_and_future_overlap_are_rejected(self):
		self.approve(from_date=nowdate())
		with self.assertRaisesRegex(frappe.ValidationError, "same From Date"):
			self.make_price(from_date=nowdate()).insert(ignore_permissions=True).submit()

		future = add_days(nowdate(), 20)
		self.approve(supplier=self.suppliers[1], from_date=future)
		with self.assertRaisesRegex(frappe.ValidationError, "exists from"):
			self.make_price(
				supplier=self.suppliers[1],
				from_date=nowdate(),
				to_date=add_days(future, 1),
			).insert(ignore_permissions=True).submit()

	def test_native_approval_rechecks_blank_supplier(self):
		doc = self.make_price().insert(ignore_permissions=True)
		frappe.db.set_value(
			"YRP Item Price",
			doc.name,
			{"supplier": None, "workflow_state": "Approval Pending"},
			update_modified=False,
		)
		frappe.set_user(self.purchase_manager)
		with self.assertRaisesRegex(frappe.ValidationError, "Supplier is required"):
			apply_workflow(frappe.get_doc("YRP Item Price", doc.name), "Approve")

	def test_reject_only_transition_allows_invalid_migrated_draft(self):
		doc = self.make_price().insert(ignore_permissions=True)
		frappe.db.set_value(
			"YRP Item Price",
			doc.name,
			{"supplier": None, "workflow_state": "Approval Pending"},
			update_modified=False,
		)
		frappe.set_user(self.purchase_manager)

		apply_workflow(frappe.get_doc("YRP Item Price", doc.name), "Reject")

		doc.reload()
		self.assertEqual(doc.workflow_state, "Rejected")
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(get_transitions(doc), [])

	def test_reject_only_transition_allows_missing_tiers_and_broken_links(self):
		doc = self.make_price().insert(ignore_permissions=True)
		missing = f"_Test Missing Price Link {frappe.generate_hash(length=8)}"
		frappe.db.delete("YRP Item Price Value", {"parent": doc.name})
		frappe.db.set_value(
			"YRP Item Price",
			doc.name,
			{
				"item_name": missing,
				"supplier": None,
				"tax": missing,
				"workflow_state": "Approval Pending",
			},
			update_modified=False,
		)
		frappe.set_user(self.purchase_manager)

		apply_workflow(frappe.get_doc("YRP Item Price", doc.name), "Reject")

		doc.reload()
		self.assertEqual((doc.workflow_state, doc.docstatus), ("Rejected", 0))
		self.assertEqual(doc.item_price_values, [])

	def test_reject_only_transition_allows_a_retained_broken_tier_link(self):
		doc = self.make_price().insert(ignore_permissions=True)
		missing = f"_Test Missing Attribute Value {frappe.generate_hash(length=8)}"
		frappe.db.set_value(
			"YRP Item Price Value",
			doc.item_price_values[0].name,
			"attribute_value",
			missing,
			update_modified=False,
		)
		frappe.db.set_value(
			"YRP Item Price",
			doc.name,
			{"supplier": None, "workflow_state": "Approval Pending"},
			update_modified=False,
		)
		frappe.set_user(self.purchase_manager)

		apply_workflow(frappe.get_doc("YRP Item Price", doc.name), "Reject")

		doc.reload()
		self.assertEqual((doc.workflow_state, doc.docstatus), ("Rejected", 0))
		self.assertEqual(doc.item_price_values[0].attribute_value, missing)

	def test_reject_transition_cannot_hide_a_price_data_change(self):
		doc = self.make_price().insert(ignore_permissions=True)
		frappe.db.set_value(
			"YRP Item Price",
			doc.name,
			{"supplier": None, "workflow_state": "Approval Pending"},
			update_modified=False,
		)
		frappe.set_user(self.purchase_manager)
		doc.reload()
		doc.workflow_state = "Rejected"
		doc.from_date = add_days(doc.from_date, -1)

		with self.assertRaisesRegex(frappe.ValidationError, "Supplier is required"):
			doc.save()

	def test_reject_transition_cannot_hide_approved_by_change(self):
		self._assert_reject_change_is_validated(
			"approved_by", f"missing-user-{frappe.generate_hash(length=8)}@example.test"
		)

	def test_reject_transition_cannot_hide_amended_from_change(self):
		self._assert_reject_change_is_validated(
			"amended_from", f"_Test Missing Price {frappe.generate_hash(length=8)}"
		)

	def test_reject_transition_cannot_hide_supplier_name_change(self):
		self._assert_reject_change_is_validated("supplier_name", "Corrupted supplier label")

	def _assert_reject_change_is_validated(self, fieldname, value):
		doc = self.make_price().insert(ignore_permissions=True)
		frappe.db.set_value(
			"YRP Item Price",
			doc.name,
			{"supplier": None, "workflow_state": "Approval Pending"},
			update_modified=False,
		)
		frappe.set_user(self.purchase_manager)
		doc.reload()
		doc.workflow_state = "Rejected"
		doc.set(fieldname, value)

		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_manual_submitted_to_date_edit_is_rejected(self):
		doc = self.approve()
		doc.to_date = add_days(nowdate(), 10)

		with self.assertRaisesRegex(frappe.ValidationError, "cannot be changed"):
			doc.save(ignore_permissions=True)

	def test_approval_rechecks_current_item_stock_uom(self):
		doc = self.make_price().insert(ignore_permissions=True)
		frappe.db.set_value("Item", self.item, "stock_uom", self.other_uom, update_modified=False)
		try:
			with self.assertRaisesRegex(frappe.ValidationError, "stock UOM"):
				doc.submit()
		finally:
			frappe.db.set_value("Item", self.item, "stock_uom", self.stock_uom, update_modified=False)

	def test_workflow_roles_and_rejected_final_state(self):
		doc = self.make_price(supplier=self.suppliers[2]).insert(ignore_permissions=True)
		frappe.set_user(self.purchase_user)
		apply_workflow(frappe.get_doc("YRP Item Price", doc.name), "Submit")
		with self.assertRaisesRegex(frappe.ValidationError, "Not a valid Workflow Action"):
			apply_workflow(frappe.get_doc("YRP Item Price", doc.name), "Approve")

		frappe.set_user(self.purchase_manager)
		apply_workflow(frappe.get_doc("YRP Item Price", doc.name), "Reject")
		doc.reload()
		self.assertEqual(doc.workflow_state, "Rejected")
		self.assertEqual(get_transitions(doc), [])

	def test_workflow_approve_and_expire_transitions(self):
		doc = self.make_price(supplier=self.suppliers[2]).insert(ignore_permissions=True)
		frappe.set_user(self.purchase_user)
		apply_workflow(frappe.get_doc("YRP Item Price", doc.name), "Submit")

		frappe.set_user(self.purchase_manager)
		apply_workflow(frappe.get_doc("YRP Item Price", doc.name), "Approve")
		doc.reload()
		self.assertEqual((doc.workflow_state, doc.docstatus), ("Approved", 1))

		apply_workflow(doc, "Expired")
		doc.reload()
		self.assertEqual((doc.workflow_state, doc.docstatus), ("Expired", 2))


class TestItemPriceConcurrency(TestCase):
	def setUp(self):
		self.run_token = frappe.generate_hash(length=12)
		self.addCleanup(
			_cleanup_fixture_run,
			frappe.local.site,
			frappe.local.sites_path,
			self.run_token,
		)

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

	def _make_fixture(self, from_dates):
		def create():
			suffix = f"{self.run_token}-{frappe.generate_hash(length=6)}"
			uom = _ensure_uom(f"Race Unit {suffix}")
			item = frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": f"_Test Price Race Item {suffix}",
					"item_name": f"_Test Price Race Item {suffix}",
					"item_group": _item_group(),
					"stock_uom": uom,
					"is_stock_item": 1,
					"is_purchase_item": 1,
					**_india_compliance_item_fields(),
				}
			).insert(ignore_permissions=True)
			supplier = frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": f"_Test Price Race Supplier {suffix}",
					"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
				}
			).insert(ignore_permissions=True)
			tax = frappe.db.get_value("YRP Tax Slab", {}, "name")
			prices = [
				frappe.get_doc(
					{
						"doctype": "YRP Item Price",
						"item_name": item.name,
						"supplier": supplier.name,
						"uom": uom,
						"from_date": from_date,
						"tax": tax,
						"item_price_values": [{"moq": 0, "price": 10 + index, "lead_time": 1}],
					}
				)
				.insert(ignore_permissions=True)
				.name
				for index, from_date in enumerate(from_dates)
			]
			commit_connection()
			return {
				"item": item.name,
				"supplier": supplier.name,
				"uom": uom,
				"prices": prices,
			}

		return self._run_connection(create)

	def _read_prices(self, price_names):
		return self._run_connection(
			lambda: frappe.get_all(
				"YRP Item Price",
				filters={"name": ["in", price_names]},
				fields=["name", "docstatus", "workflow_state", "from_date", "to_date"],
				order_by="from_date asc, name asc",
			)
		)

	def _cleanup_fixture(self, fixture):
		def cleanup():
			for name in fixture["prices"]:
				if frappe.db.exists("YRP Item Price", name):
					frappe.db.set_value(
						"YRP Item Price",
						name,
						{"docstatus": 0, "workflow_state": "Draft"},
						update_modified=False,
					)
					frappe.delete_doc(
						"YRP Item Price",
						name,
						force=True,
						ignore_permissions=True,
						delete_permanently=True,
					)
			for doctype, name in (
				("Item", fixture["item"]),
				("Supplier", fixture["supplier"]),
				("UOM", fixture["uom"]),
			):
				frappe.delete_doc(
					doctype,
					name,
					force=True,
					ignore_permissions=True,
					delete_permanently=True,
				)
			if fixture.get("attribute"):
				frappe.db.delete("Item Attribute Value", {"parent": fixture["attribute"]})
				for mapping in frappe.get_all(
					"YRP Item Item Attribute Mapping",
					filters={"attribute_name": fixture["attribute"]},
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
					filters={"attribute_name": fixture["attribute"]},
					pluck="name",
				):
					frappe.delete_doc(
						"YRP Item Attribute Value",
						value,
						force=True,
						ignore_permissions=True,
						delete_permanently=True,
					)
				frappe.delete_doc(
					"Item Attribute",
					fixture["attribute"],
					force=True,
					ignore_permissions=True,
					delete_permanently=True,
				)
			commit_connection()

		self._run_connection(cleanup)

	def _start_workers(self, workers, *, progress=None):
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

	def _mark_approved(self, fixture, index, *, to_date=None):
		def update():
			frappe.db.set_value(
				"YRP Item Price",
				fixture["prices"][index],
				{
					"docstatus": 1,
					"workflow_state": "Approved",
					"to_date": to_date,
				},
				update_modified=False,
			)
			commit_connection()

		self._run_connection(update)

	def _assert_threads_finished(self, threads, results):
		for thread in threads.values():
			thread.join(timeout=20)
		self.assertTrue(all(not thread.is_alive() for thread in threads.values()), results)

	def test_equal_start_approvals_serialize_across_real_connections(self):
		fixture = self._make_fixture([nowdate(), nowdate()])
		site = frappe.local.site
		sites_path = frappe.local.sites_path

		barrier = threading.Barrier(2)
		result_lock = threading.Lock()
		results = []

		def approve(price_name):
			frappe.init(site, sites_path=sites_path)
			frappe.connect()
			frappe.flags.in_test = True
			frappe.set_user("Administrator")
			try:
				doc = frappe.get_doc("YRP Item Price", price_name)
				barrier.wait(timeout=10)
				doc.submit()
				commit_connection()
				outcome = (price_name, "submitted", "")
			except Exception as error:
				frappe.db.rollback()
				outcome = (price_name, "failed", f"{error}\n{traceback.format_exc()}")
			finally:
				frappe.destroy()
			with result_lock:
				results.append(outcome)

		threads = [threading.Thread(target=approve, args=(name,), daemon=True) for name in fixture["prices"]]
		try:
			for thread in threads:
				thread.start()
			for thread in threads:
				thread.join(timeout=20)
			self.assertTrue(all(not thread.is_alive() for thread in threads), results)
			self.assertEqual([row[1] for row in results].count("submitted"), 1, results)
			self.assertEqual([row[1] for row in results].count("failed"), 1, results)
			failed = next(row for row in results if row[1] == "failed")
			self.assertIn("same From Date", failed[2])
			self.assertEqual(
				len(
					[
						row
						for row in self._read_prices(fixture["prices"])
						if row.docstatus == 1 and row.workflow_state == "Approved"
					]
				),
				1,
			)
		finally:
			self._cleanup_fixture(fixture)

	def test_sequential_versions_use_current_predecessor_snapshot(self):
		fixture = self._make_fixture([nowdate(), add_days(nowdate(), 1)])
		site = frappe.local.site
		sites_path = frappe.local.sites_path
		item_locked = threading.Event()
		younger_loaded = threading.Event()
		result_lock = threading.Lock()
		results = []

		def record(price_name, status, detail=""):
			with result_lock:
				results.append((price_name, status, detail))

		def approve_older():
			frappe.init(site, sites_path=sites_path)
			frappe.connect()
			frappe.flags.in_test = True
			frappe.set_user("Administrator")
			try:
				frappe.db.get_value("Item", fixture["item"], "name", for_update=True)
				item_locked.set()
				if not younger_loaded.wait(timeout=10):
					raise TimeoutError("Younger approval did not load its snapshot")
				doc = frappe.get_doc("YRP Item Price", fixture["prices"][0])
				doc.submit()
				commit_connection()
				record(doc.name, "submitted")
			except Exception as error:
				frappe.db.rollback()
				record(fixture["prices"][0], "failed", f"{error}\n{traceback.format_exc()}")
			finally:
				frappe.destroy()

		def approve_younger():
			frappe.init(site, sites_path=sites_path)
			frappe.connect()
			frappe.flags.in_test = True
			frappe.set_user("Administrator")
			try:
				if not item_locked.wait(timeout=10):
					raise TimeoutError("Older approval did not lock the Item")
				doc = frappe.get_doc("YRP Item Price", fixture["prices"][1])
				younger_loaded.set()
				doc.submit()
				commit_connection()
				record(doc.name, "submitted")
			except Exception as error:
				frappe.db.rollback()
				record(fixture["prices"][1], "failed", f"{error}\n{traceback.format_exc()}")
			finally:
				frappe.destroy()

		threads = [
			threading.Thread(target=approve_older, daemon=True),
			threading.Thread(target=approve_younger, daemon=True),
		]
		try:
			for thread in threads:
				thread.start()
			for thread in threads:
				thread.join(timeout=20)
			self.assertTrue(all(not thread.is_alive() for thread in threads), results)
			self.assertEqual([row[1] for row in results].count("submitted"), 2, results)
			prices = self._read_prices(fixture["prices"])
			self.assertEqual(
				[(row.docstatus, row.workflow_state) for row in prices],
				[(1, "Approved"), (1, "Approved")],
			)
			self.assertEqual(prices[0].to_date, frappe.utils.getdate(nowdate()))
			self.assertIsNone(prices[1].to_date)
		finally:
			self._cleanup_fixture(fixture)

	def test_approval_does_not_wait_for_locked_pending_draft(self):
		fixture = self._make_fixture([nowdate(), add_days(nowdate(), 1)])
		pending_locked = threading.Event()
		approval_finished = threading.Event()

		def hold_pending():
			frappe.get_doc("YRP Item Price", fixture["prices"][0], for_update=True)
			pending_locked.set()
			if not approval_finished.wait(10):
				raise TimeoutError("Approval waited on an unrelated pending draft")
			frappe.db.rollback()

		def approve_other():
			if not pending_locked.wait(10):
				raise TimeoutError("Pending draft was not locked")
			try:
				doc = frappe.get_doc("YRP Item Price", fixture["prices"][1])
				doc.submit()
				commit_connection()
				return doc.name
			finally:
				approval_finished.set()

		threads, results = self._start_workers({"pending": hold_pending, "approval": approve_other})
		try:
			self._assert_threads_finished(threads, results)
			self.assertEqual(results.get("pending", (None,))[0], "ok", results)
			self.assertEqual(results.get("approval", (None,))[0], "ok", results)
			rows = self._read_prices(fixture["prices"])
			self.assertEqual(
				[(row.workflow_state, row.docstatus) for row in rows],
				[("Draft", 0), ("Approved", 1)],
			)
		finally:
			approval_finished.set()
			self._cleanup_fixture(fixture)

	def test_approval_recovers_while_batch_expiry_processes_predecessor(self):
		fixture = self._make_fixture([add_days(nowdate(), -10), nowdate()])
		other = self._make_fixture([add_days(nowdate(), -20)])
		self._mark_approved(fixture, 0, to_date=add_days(nowdate(), -1))
		self._mark_approved(other, 0, to_date=add_days(nowdate(), -1))
		predecessor_locked = threading.Event()
		approval_scan_started = threading.Event()
		release_expiry = threading.Event()
		progress = threading.local()
		database_class = type(frappe.local.db)
		original_sql = database_class.sql
		original_get_all = database_class.get_all
		expiry_prices = [fixture["prices"][0], other["prices"][0]]

		def scoped_get_all(database, doctype, *args, **kwargs):
			if (
				getattr(progress, "operation", None) == "expiry"
				and doctype == "YRP Item Price"
				and kwargs.get("pluck") == "name"
			):
				return expiry_prices
			return original_get_all(database, doctype, *args, **kwargs)

		def observe_sql(database, query, *args, **kwargs):
			lowered = query.lower() if isinstance(query, str) else ""
			operation = getattr(progress, "operation", None)
			if (
				operation == "approval"
				and "tabyrp item price" in lowered
				and "for update" in lowered
				and "item_name" in lowered.split("where", 1)[-1]
			):
				approval_scan_started.set()
			result = original_sql(database, query, *args, **kwargs)
			if (
				operation == "expiry"
				and "tabyrp item price" in lowered
				and "for update" in lowered
				and fixture["prices"][0] in repr(args)
				and not predecessor_locked.is_set()
			):
				predecessor_locked.set()
				if not release_expiry.wait(10):
					raise TimeoutError("Expiry row lock was not released")
			return result

		def expire_batch():
			update_all_expired_item_price()
			return "finished"

		def approve_new():
			if not predecessor_locked.wait(10):
				raise TimeoutError("Batch expiry did not lock the predecessor")
			doc = frappe.get_doc("YRP Item Price", fixture["prices"][1])
			doc.submit()
			commit_connection()
			return doc.name

		try:
			with (
				patch.object(database_class, "sql", new=observe_sql),
				patch.object(database_class, "get_all", new=scoped_get_all),
			):
				threads, results = self._start_workers(
					{"expiry": expire_batch, "approval": approve_new}, progress=progress
				)
				self.assertTrue(approval_scan_started.wait(10), results)
				release_expiry.set()
				self._assert_threads_finished(threads, results)
			self.assertEqual(results.get("expiry", (None,))[0], "ok", results)
			self.assertEqual(results.get("approval", (None,))[0], "ok", results)
			rows = self._read_prices(fixture["prices"])
			self.assertEqual((rows[1].workflow_state, rows[1].docstatus), ("Approved", 1), rows)
			self.assertIn(
				(rows[0].workflow_state, rows[0].docstatus, rows[0].to_date),
				{
					("Expired", 2, frappe.utils.getdate(add_days(nowdate(), -1))),
					("Approved", 1, frappe.utils.getdate(add_days(nowdate(), -1))),
				},
			)
			active = [
				row
				for row in rows
				if row.workflow_state == "Approved"
				and row.docstatus == 1
				and (not row.to_date or row.to_date >= frappe.utils.getdate(nowdate()))
			]
			self.assertEqual([row.name for row in active], [fixture["prices"][1]])
			other_row = self._read_prices(other["prices"])[0]
			self.assertEqual((other_row.workflow_state, other_row.docstatus), ("Expired", 2))
		finally:
			release_expiry.set()
			self._cleanup_fixture(fixture)
			self._cleanup_fixture(other)

	def test_cancel_approval_deadlock_rolls_back_loser_and_never_overlaps(self):
		from essdee_yrp.purchase import item_price_workspace

		fixture = self._make_fixture([add_days(nowdate(), -10), nowdate()])
		self._mark_approved(fixture, 0)
		self._run_connection(
			lambda: (
				frappe.db.set_value(
					"YRP Item Price",
					fixture["prices"][1],
					"workflow_state",
					"Approval Pending",
					update_modified=False,
				),
				commit_connection(),
			)
		)
		predecessor_locked = threading.Event()
		approval_scan_started = threading.Event()
		release_expiry = threading.Event()
		progress = threading.local()
		database_class = type(frappe.local.db)
		original_sql = database_class.sql
		original_execute_once = item_price_workspace._execute_item_price_action_once
		attempts = 0
		attempt_lock = threading.Lock()

		def observe_sql(database, query, *args, **kwargs):
			lowered = query.lower() if isinstance(query, str) else ""
			operation = getattr(progress, "operation", None)
			if (
				operation == "approval"
				and "tabyrp item price" in lowered
				and "for update" in lowered
				and "item_name" in lowered.split("where", 1)[-1]
			):
				approval_scan_started.set()
			result = original_sql(database, query, *args, **kwargs)
			if (
				operation == "expiry"
				and "tabyrp item price" in lowered
				and "for update" in lowered
				and fixture["prices"][0] in repr(args)
				and not predecessor_locked.is_set()
			):
				predecessor_locked.set()
				if not release_expiry.wait(10):
					raise TimeoutError("Expire row lock was not released")
			return result

		def count_attempts(*args, **kwargs):
			nonlocal attempts
			with attempt_lock:
				attempts += 1
			return original_execute_once(*args, **kwargs)

		def expire_predecessor():
			doc = frappe.get_doc("YRP Item Price", fixture["prices"][0])
			apply_workflow(doc, "Expired")
			commit_connection()
			return doc.name

		def approve_new():
			if not predecessor_locked.wait(10):
				raise TimeoutError("Expire did not lock the predecessor")
			modified = frappe.db.get_value("YRP Item Price", fixture["prices"][1], "modified")
			payload = item_price_workspace.execute_item_price_action(
				fixture["prices"][1], "Approve", modified
			)
			commit_connection()
			return payload["record"]["workflow_state"]

		try:
			with (
				patch.object(database_class, "sql", new=observe_sql),
				patch.object(
					item_price_workspace,
					"_execute_item_price_action_once",
					side_effect=count_attempts,
				),
				patch.object(item_price_workspace.time, "sleep"),
			):
				threads, results = self._start_workers(
					{"expiry": expire_predecessor, "approval": approve_new},
					progress=progress,
				)
				self.assertTrue(approval_scan_started.wait(10), results)
				release_expiry.set()
				self._assert_threads_finished(threads, results)
			self.assertEqual(results.get("approval", (None,))[0], "ok", results)
			self.assertIn(results.get("expiry", (None,))[0], {"ok", "QueryDeadlockError"}, results)
			self.assertTrue(attempts == 2 or results["expiry"][0] == "QueryDeadlockError", results)
			rows = self._read_prices(fixture["prices"])
			predecessor, approved = rows
			self.assertEqual((approved.workflow_state, approved.docstatus), ("Approved", 1))
			valid_predecessor_states = {
				("Expired", 2, None),
				("Approved", 1, frappe.utils.getdate(add_days(nowdate(), -1))),
			}
			self.assertIn(
				(predecessor.workflow_state, predecessor.docstatus, predecessor.to_date),
				valid_predecessor_states,
			)
			active = [
				row
				for row in rows
				if row.workflow_state == "Approved"
				and row.docstatus == 1
				and (not row.to_date or row.to_date >= frappe.utils.getdate(nowdate()))
			]
			self.assertEqual([row.name for row in active], [fixture["prices"][1]])
		finally:
			release_expiry.set()
			self._cleanup_fixture(fixture)
