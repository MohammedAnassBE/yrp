"""Desk and API action permissions over fictional Contact-derived memberships."""
import unittest

import frappe

from yrp.yrp_retail import api
from yrp.yrp_retail.test_retail_flow import TestRetailFlow
from yrp.yrp_retail.tests.fixtures import sales_person


class TestSalesPersonRole(unittest.TestCase):
	def setUp(self):
		self.fixture = TestRetailFlow()
		# Rollback restores DocPerm rows, but not the metadata cached while an
		# If Owner rule was active. Clear it after the fixture rollback.
		self.addCleanup(frappe.clear_cache, doctype="YRP Retailer")
		self.addCleanup(self.fixture.doCleanups)
		self.fixture.setUp()

	def test_native_retailer_and_own_visit_can_be_created_and_edited(self):
		f = self.fixture
		retailer = frappe.get_doc("YRP Retailer", f.retailer())
		retailer.shop_name = "Updated Fictional Shop"
		retailer.save()
		self.assertEqual(retailer.reload().shop_name, "Updated Fictional Shop")
		visit = frappe.get_doc("YRP Visit", f.visit())
		self.assertTrue(frappe.has_permission("YRP Visit", "read", visit))
		self.assertTrue(frappe.has_permission("YRP Visit", "write", visit))
		visit.notes = "Corrected visit details"
		visit.save()
		self.assertEqual(visit.reload().notes, "Corrected visit details")
		api.create_retail_order(visit.name, f.items())
		visit.reload()
		visit.notes = "Order follow-up"
		visit.save()
		self.assertEqual(visit.reload().notes, "Order follow-up")
		visit.visit_datetime = frappe.utils.add_to_date(visit.visit_datetime, days=1)
		with self.assertRaises(frappe.ValidationError):
			visit.save()
		with self.assertRaises(frappe.PermissionError):
			retailer.delete(ignore_permissions=True)

	def test_draft_order_edit_submit_and_cancel_through_native_lifecycle(self):
		f = self.fixture
		name = f.order()
		api.update_retail_order(name, f.items(4))
		self.assertEqual(frappe.get_doc("YRP Retail Order", name).items[0].qty, 4)
		self.assertEqual(api.submit_retail_order(name)["docstatus"], 1)
		with self.assertRaises(frappe.PermissionError):
			api.update_retail_order(name, f.items(5))
		self.assertEqual(api.cancel_retail_order(name)["docstatus"], 2)

	def test_summary_draft_edit_submit_cancel_and_claim_release(self):
		f = self.fixture
		order = f.order()
		name = api.create_summary([order])["name"]
		api.update_summary(name, [{"item_code": f.item.name, "uom": f.uom.name, "customer_stock_qty": 1}])
		api.submit_summary(name)
		doc = frappe.get_doc("YRP Retail Order Summary", name)
		doc.items[0].customer_stock_qty = 2
		with self.assertRaises(frappe.PermissionError):
			doc.save(ignore_permissions=True)
		self.assertEqual(api.cancel_summary(name)["docstatus"], 2)
		self.assertFalse(frappe.db.get_value("YRP Retail Order", order, "summary"))

	def test_shared_retailer_edit_preserves_original_person_after_assignment_revoked(self):
		f = self.fixture
		shop = f.retailer()
		frappe.set_user("Administrator")
		colleague = sales_person(f.customer)
		user = frappe.get_doc({"doctype": "User", "first_name": "Fictional Colleague",
			"email": frappe.generate_hash(length=12) + "@example.invalid", "send_welcome_email": 0,
			"roles": [{"role": "YRP Partner"}, {"role": "YRP Sales Person"}]}).insert()
		frappe.get_doc({"doctype": "Contact", "first_name": f.label("Colleague Contact"),
			"email_ids": [{"email_id": user.email, "is_primary": 1}],
			"links": [{"link_doctype": "Sales Person", "link_name": colleague.name}]}).insert()
		f.person.set("yrp_customers", [])
		f.person.save()
		frappe.set_user(user.name)
		retailer = frappe.get_doc("YRP Retailer", shop)
		retailer.shop_name = "Updated by assigned colleague"
		retailer.save()
		self.assertEqual(retailer.sales_person, f.person.name)
		retailer.sales_person = colleague.name
		with self.assertRaises(frappe.PermissionError):
			retailer.save(ignore_permissions=True)

	def test_other_person_order_cannot_be_stolen_and_assignment_revocation_blocks_writes(self):
		f = self.fixture
		name = f.order()
		visit_name = f.visit()
		frappe.set_user("Administrator")
		other = sales_person(f.customer)
		visit = frappe.get_doc("YRP Visit", visit_name)
		# The actor API still selects f.person; create the colleague's visit natively.
		visit = frappe.copy_doc(visit)
		visit.sales_person = other.name
		visit.insert()
		order = frappe.get_doc({"doctype": "YRP Retail Order", "visit": visit.name,
			"order_type": visit.visit_type, "sales_person": visit.sales_person,
			"customer": visit.customer, "retailer": visit.retailer,
			"items": f.items()}).insert()
		visit.reload()  # Order creation updates the derived Visit flag and timestamp.
		frappe.set_user(f.user.name)
		self.assertFalse(frappe.has_permission("YRP Visit", "write", visit))
		visit.notes = "Unrelated visit edit"
		with self.assertRaises(frappe.PermissionError):
			visit.save(ignore_permissions=True)
		self.assertNotIn(order.name, frappe.get_list("YRP Retail Order", pluck="name"))
		order.visit = frappe.db.get_value("YRP Retail Order", name, "visit")
		order.sales_person = f.person.name
		with self.assertRaises(frappe.PermissionError):
			order.save(ignore_permissions=True)
		frappe.set_user("Administrator")
		f.person.set("yrp_customers", [])
		f.person.save()
		frappe.set_user(f.user.name)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("YRP Retail Order", name).save(ignore_permissions=True)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("YRP Visit", visit_name).save(ignore_permissions=True)

	def test_action_role_without_partner_role_fails_closed(self):
		f = self.fixture
		name = f.retailer()
		frappe.set_user("Administrator")
		f.user.set("roles", [{"role": "YRP Sales Person"}])
		f.user.save()
		frappe.set_user(f.user.name)
		self.assertEqual(frappe.get_list("YRP Retailer", pluck="name"), [])
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc("YRP Retailer", name).save(ignore_permissions=True)

	def test_draft_discard_cannot_bypass_cancel_and_claim_rules(self):
		name = self.fixture.order()
		summary = api.create_summary([name])["name"]
		for doctype, record in (("YRP Retail Order", name), ("YRP Retail Order Summary", summary)):
			with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
				frappe.get_doc(doctype, record).discard()
			self.assertEqual(frappe.db.get_value(doctype, record, "docstatus"), 0)
		self.assertEqual(frappe.db.get_value("YRP Retail Order", name, "summary"), summary)

	def test_cancellation_cannot_edit_submitted_quantities(self):
		f = self.fixture
		name = f.order()
		api.submit_retail_order(name)
		doc = frappe.get_doc("YRP Retail Order", name)
		doc.items[0].qty = 99
		with self.assertRaises(frappe.PermissionError):
			doc.cancel()
		doc.reload()
		self.assertEqual((doc.docstatus, doc.items[0].qty), (1, 10))
		doc.cancel()

	def test_count_entry_requires_a_separate_permission(self):
		f = self.fixture
		with self.assertRaises(frappe.PermissionError):
			api.update_customer_stock(f.customer.name, f.item.name, 5)

	def test_blank_desk_form_is_editable_but_unassigned_insert_is_denied(self):
		from frappe.permissions import get_doc_permissions
		doc = frappe.new_doc("YRP Retailer")
		self.assertTrue(get_doc_permissions(doc).get("create"))
		self.assertTrue(get_doc_permissions(doc).get("write"))
		self.assertFalse(frappe.has_permission(doc.doctype, "create", doc))

	def test_has_order_is_read_only_and_derived_from_linked_order(self):
		f = self.fixture
		result = api.create_visit("Primary", frappe.utils.now_datetime(), 12, 77,
			customer=f.customer.name)
		visit = frappe.get_doc("YRP Visit", result['name'])
		self.assertEqual(visit.has_order, 0)
		self.assertTrue(visit.meta.get_field("has_order").read_only)
		visit.has_order = 1
		visit.save()
		self.assertEqual(visit.reload().has_order, 0)
		api.create_retail_order(visit.name, f.items())
		visit.reload()
		visit.has_order = 0
		visit.save()
		self.assertEqual(visit.reload().has_order, 1)

	def test_if_owner_permission_uses_stored_owner(self):
		f = self.fixture
		own = frappe.get_doc("YRP Retailer", f.retailer())
		frappe.set_user("Administrator")
		other = frappe.get_doc({"doctype": "YRP Retailer", "retailer_name": f.label("Managed Shop"),
			"shop_name": f.label("Managed Shop Name"),
			"customer": f.customer.name, "sales_person": f.person.name}).insert()
		permission = frappe.get_doc("Custom DocPerm", {"parent": "YRP Retailer", "role": "YRP Sales Person"})
		permission.if_owner = 1
		permission.save()
		frappe.clear_cache(doctype="YRP Retailer")
		frappe.set_user(f.user.name)
		own.shop_name = "Owner edit"
		own.save()
		other.owner = f.user.name
		other.shop_name = "Forged owner edit"
		with self.assertRaises(frappe.PermissionError):
			other.save(ignore_permissions=True)
