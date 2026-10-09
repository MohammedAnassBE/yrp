"""Real document/API checks; fictional data rolls back after every test."""
import frappe
from frappe.utils import now_datetime

from yrp.yrp_retail import api
from yrp.yrp_retail.test_retail_flow import TestRetailFlow
from yrp.yrp_retail.tests.fixtures import sales_person


class TestSharedRetailers(TestRetailFlow):
	def test_same_shop_names_keep_distinct_ids_without_gstin(self):
		names = []
		for _ in range(4):
			names.append(api.create_retailer("Fictional Owner", self.customer.name,
				shop_name="Fictional Shop")["name"])
		self.assertEqual(len(set(names)), 4)
		for name in names:
			self.assertRegex(name, r"^RET-\d{3,}$")
		self.assertFalse(frappe.get_meta("YRP Retailer").has_field("gstin"))

	def test_address_without_indian_gstin(self):
		frappe.set_user("Administrator")
		address = frappe.get_doc(dict(doctype="Address", address_title=self.label("Address"),
			address_type="Shipping", address_line1="1 Fictional Street", city="Fictional City",
			country="United States", pincode="10001")).insert()
		doc = frappe.get_doc(dict(doctype="YRP Retailer", retailer_name=self.label("Owner"),
			shop_name=self.label("Shop"), customer=self.customer.name, sales_person=self.person.name,
			primary_address=address.name)).insert()
		doc.reload()
		self.assertEqual(doc.primary_address, address.name)

	def test_one_contact_grants_only_linked_shops_and_unlink_revokes(self):
		names = [self.retailer() for _ in range(3)]
		frappe.set_user("Administrator")
		frappe.get_doc(dict(doctype="YRP Partner Type", partner_type_name=self.label("Retail Membership"),
			reference_doctype="YRP Retailer")).insert()
		user = frappe.get_doc(dict(doctype="User", email=frappe.generate_hash(length=12)+"@example.invalid",
			first_name="Fictional Shop Owner", send_welcome_email=0, roles=[dict(role="YRP Partner")])).insert()
		contact = frappe.get_doc(dict(doctype="Contact", first_name=self.label("Shop Contact"), user=user.name,
			email_ids=[dict(email_id=user.name, is_primary=1)],
			phone_nos=[dict(phone="2025550100", is_primary_mobile_no=1)],
			links=[dict(link_doctype="YRP Retailer", link_name=n) for n in names[:2]])).insert()
		frappe.set_user(user.name)
		self.assertEqual(set(frappe.get_list("YRP Retailer", pluck="name")), set(names[:2]))
		self.assertTrue(frappe.has_permission("YRP Retailer", "read", frappe.get_doc("YRP Retailer", names[1])))
		self.assertFalse(frappe.has_permission("YRP Retailer", "read", frappe.get_doc("YRP Retailer", names[2])))
		frappe.set_user("Administrator")
		contact.set("links", [dict(link_doctype="YRP Retailer", link_name=names[0])])
		contact.save()
		frappe.set_user(user.name)
		self.assertEqual(frappe.get_list("YRP Retailer", pluck="name"), [names[0]])

	def test_shared_visit_order_and_customer_revocation(self):
		retailer = self.retailer()
		frappe.set_user("Administrator")
		second = sales_person(self.customer)
		user = frappe.get_doc(dict(doctype="User", email=frappe.generate_hash(length=12)+"@example.invalid",
			first_name="Fictional Visiting Person", send_welcome_email=0,
			roles=[dict(role="YRP Partner"), dict(role="YRP Sales Person")])).insert()
		frappe.get_doc(dict(doctype="Contact", first_name=self.label("Visitor Contact"), user=user.name,
			email_ids=[dict(email_id=user.email, is_primary=1)],
			links=[dict(link_doctype="Sales Person", link_name=second.name)])).insert()
		frappe.set_user(user.name)
		self.assertIn(retailer, [r.name for r in api.list_records("YRP Retailer")])
		self.assertTrue(frappe.has_permission("YRP Retailer", "read", frappe.get_doc("YRP Retailer", retailer)))
		visit = api.create_visit("Secondary", str(now_datetime()), 12.5, 77.5, retailer=retailer)["name"]
		order = api.create_retail_order(visit, self.items())["name"]
		self.assertEqual(frappe.db.get_value("YRP Retail Order", order, "sales_person"), second.name)
		frappe.set_user(self.user.name)
		with self.assertRaises(frappe.PermissionError):
			api.update_retail_order(order, self.items(2))
		# Revoke both people, including the retailer's original creator. Neither
		# a direct sales_person link nor the API may preserve obsolete access.
		for person, login in ((second, user.name), (self.person, self.user.name)):
			frappe.set_user("Administrator")
			person.set("yrp_customers", [])
			person.save()
			frappe.set_user(login)
			self.assertNotIn(retailer, frappe.get_list("YRP Retailer", pluck="name"))
			self.assertFalse(frappe.has_permission("YRP Retailer", "read", frappe.get_doc("YRP Retailer", retailer)))
			self.assertFalse(api.list_records("YRP Retailer"))
			with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
				api.create_visit("Secondary", str(now_datetime()), 12.5, 77.5, retailer=retailer)
