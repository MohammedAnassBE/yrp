"""Customer authorization tests use fictional records and roll back every row.

Minimal transaction rows isolate the permission layer from posting side effects;
memberships and Contacts use the ordinary application synchronization hooks.
"""

import unittest
from unittest.mock import patch

import frappe

from yrp.yrp_partner import customer_access as access
from yrp.yrp_partner.permissions import prevent_partner_write
from yrp.yrp_retail.tests.fixtures import customer, sales_person


class TestCustomerAccess(unittest.TestCase):
	def setUp(self):
		self.point = "customer_access_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(self.point)
		self.addCleanup(frappe.db.rollback, save_point=self.point)
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Administrator")
		self.types = {}
		for doctype in ("Customer", "Sales Person", "Sales Partner", "YRP Retailer"):
			# Never backfill real site masters while constructing a test type.
			with patch("yrp.yrp_partner.backfill.sync_partner_type"):
				self.types[doctype] = frappe.get_doc({
					"doctype": "YRP Partner Type", "reference_doctype": doctype,
					"partner_type_name": "Test Customer Scope " + frappe.generate_hash(length=12),
				}).insert()
		self.own = customer()
		self.foreign = customer()
		self.company = self.row("Company", company_name="Fictional Scope Company " + frappe.generate_hash(length=10))
		for party in (self.own, self.foreign):
			self.row("Party Account", parent=party.name, parenttype="Customer", parentfield="accounts", company=self.company.name)
		self.user = self.make_user("YRP Partner", "YRP Customer")
		self.contact = self.link(self.user, "Customer", self.own.name)

	def make_user(self, *roles):
		return frappe.get_doc({
			"doctype": "User", "email": "customer-scope-" + frappe.generate_hash(length=12) + "@example.invalid",
			"first_name": "Fictional Customer Scope", "send_welcome_email": 0,
			"roles": [{"role": role} for role in roles],
		}).insert()

	def link(self, user, doctype, name):
		return frappe.get_doc({
			"doctype": "Contact", "first_name": "Fictional Customer Contact " + frappe.generate_hash(length=10),
			"user": user.name, "email_ids": [{"email_id": user.email, "is_primary": 1}],
			"links": [{"link_doctype": doctype, "link_name": name}],
		}).insert()

	def row(self, doctype, **values):
		"""Insert only synthetic query fixtures; do not trigger financial posting."""
		if hasattr(self, "company") and frappe.get_meta(doctype).has_field("company"):
			values.setdefault("company", self.company.name)
		doc = frappe.get_doc({"doctype": doctype,
			"name": "Test Customer Scope " + frappe.generate_hash(length=16),
			"docstatus": 0, **values})
		doc.db_insert()
		return doc

	def visible(self, doctype, user=None):
		condition = access.customer_query_condition(doctype, user or self.user.name)
		return set(frappe.db.sql(
			f"SELECT name FROM `tab{doctype}` WHERE {condition}", pluck=True))

	def test_direct_customer_and_multiple_contact_memberships_are_unioned(self):
		second = self.link(self.user, "Customer", self.foreign.name)
		duplicate = self.link(self.user, "Customer", self.own.name)
		self.assertEqual(access.customer_names(self.user.name), {self.own.name, self.foreign.name})
		self.contact.set("email_ids", [])
		self.contact.save()
		self.assertEqual(access.customer_names(self.user.name), {self.own.name, self.foreign.name})
		duplicate.set("email_ids", [])
		duplicate.save()
		self.assertEqual(access.customer_names(self.user.name), {self.foreign.name})
		second.set("links", [])
		second.save()
		self.assertEqual(access.customer_names(self.user.name), set())

	def test_role_alone_and_contact_user_field_never_grant_customer_access(self):
		unlinked = self.make_user("YRP Partner", "YRP Customer")
		only_role = self.make_user("YRP Customer")
		self.link(only_role, "Customer", self.own.name)
		for user in (unlinked, only_role):
			self.assertTrue(access.is_customer_user(user.name))
			self.assertEqual(access.customer_names(user.name), set())
			self.assertEqual(self.visible("Customer", user.name), set())
		# Leave the generated membership and Contact.user stale intentionally.
		frappe.db.delete("Contact Email", {"parent": self.contact.name, "parenttype": "Contact"})
		self.assertEqual(access.customer_names(self.user.name), set())

	def test_disabled_user_and_revoked_roles_invalidate_stale_memberships(self):
		self.assertEqual(access.customer_names(self.user.name), {self.own.name})
		frappe.db.set_value("User", self.user.name, "enabled", 0)
		self.assertEqual(access.customer_names(self.user.name), set())
		frappe.db.set_value("User", self.user.name, "enabled", 1)
		for role in ("YRP Partner", "YRP Customer"):
			with self.subTest(role=role):
				role_row = frappe.db.get_value("Has Role", {"parent": self.user.name, "role": role}, "name")
				frappe.db.set_value("Has Role", role_row, "role", "All")
				# SQL checks current roles even when Frappe's role cache is warm.
				self.assertEqual(access.customer_names(self.user.name), set())
				frappe.db.set_value("Has Role", role_row, "role", role)

	def test_contact_reassignment_revokes_old_source_before_membership_cleanup(self):
		frappe.db.set_value("Dynamic Link", self.contact.links[0].name, "link_name", self.foreign.name)
		self.assertEqual(access.customer_names(self.user.name), set())
		self.contact.reload()
		self.contact.save()
		self.assertEqual(access.customer_names(self.user.name), {self.foreign.name})

	def test_disabled_customer_retains_history_but_mismatched_partner_type_fails_closed(self):
		frappe.db.set_value("Customer", self.own.name, "disabled", 1)
		self.assertEqual(access.customer_names(self.user.name), {self.own.name})
		# The generated source must still agree with its live configuration.
		frappe.db.sql("""UPDATE `tabYRP Partner` SET reference_doctype='Sales Partner'
			WHERE reference_doctype='Customer' AND reference_name=%s""", self.own.name)
		self.assertEqual(access.customer_names(self.user.name), set())

	def test_multiple_sales_people_union_assignments_and_recheck_reassignment(self):
		user = self.make_user("YRP Partner", "YRP Sales Person")
		people = [sales_person(party) for party in (self.own, self.foreign)]
		for person in people:
			self.link(user, "Sales Person", person.name)
		self.assertEqual(access.customer_names(user.name), {self.own.name, self.foreign.name})
		# A saved assignment is no grant after its Customer changes partners.
		frappe.db.set_value("Customer", self.own.name, "default_sales_partner", self.foreign.default_sales_partner)
		self.assertEqual(access.customer_names(user.name), {self.foreign.name})
		frappe.db.set_value("Sales Person", people[1].name, "enabled", 0)
		self.assertEqual(access.customer_names(user.name), set())

	def test_multiple_sales_partners_union_all_customers_and_revoke_current_links(self):
		user = self.make_user("YRP Partner", "YRP Sales Partner")
		for party in (self.own, self.foreign):
			self.link(user, "Sales Partner", party.default_sales_partner)
		additional = customer()
		frappe.db.set_value("Customer", additional.name, "default_sales_partner", self.own.default_sales_partner)
		self.assertEqual(access.customer_names(user.name), {self.own.name, self.foreign.name, additional.name})
		frappe.db.set_value("Customer", additional.name, "default_sales_partner", "")
		self.assertEqual(access.customer_names(user.name), {self.own.name, self.foreign.name})

	def test_customer_role_does_not_use_sales_sources_without_the_matching_role(self):
		person = sales_person(self.foreign)
		self.link(self.user, "Sales Person", person.name)
		self.link(self.user, "Sales Partner", self.foreign.default_sales_partner)
		self.assertEqual(access.customer_names(self.user.name), {self.own.name})
		self.user.append("roles", {"role": "YRP Sales Person"})
		self.user.save()
		self.assertEqual(access.customer_names(self.user.name), {self.own.name, self.foreign.name})

	def test_financial_lists_and_direct_reads_agree_for_all_three_roles(self):
		own = self.row("Sales Invoice", customer=self.own.name, docstatus=1)
		foreign = self.row("Sales Invoice", customer=self.foreign.name, docstatus=1)
		person = sales_person(self.own)
		people_user = self.make_user("YRP Partner", "YRP Sales Person")
		partner_user = self.make_user("YRP Partner", "YRP Sales Partner")
		self.link(people_user, "Sales Person", person.name)
		self.link(partner_user, "Sales Partner", self.own.default_sales_partner)
		for user in (self.user, people_user, partner_user):
			with self.subTest(user=user.name):
				frappe.set_user(user.name)
				self.assertEqual(set(frappe.get_list("Sales Invoice", filters={
					"name": ["in", [own.name, foreign.name]]}, pluck="name")), {own.name})
				self.assertTrue(frappe.has_permission("Sales Invoice", "read", own))
				self.assertFalse(frappe.has_permission("Sales Invoice", "read", foreign))
				with self.assertRaises(frappe.PermissionError):
					foreign.check_permission("read")

	def test_sales_documents_include_each_native_state_and_exclude_other_customers(self):
		for doctype in ("Sales Order", "Sales Invoice", "Delivery Note"):
			with self.subTest(doctype=doctype):
				own = {self.row(doctype, customer=self.own.name, docstatus=status).name for status in (0, 1, 2)}
				foreign = self.row(doctype, customer=self.foreign.name, docstatus=1)
				self.assertTrue(own <= self.visible(doctype))
				self.assertNotIn(foreign.name, self.visible(doctype))

	def test_party_type_and_packing_slip_parent_are_enforced(self):
		for doctype in ("GL Entry", "Payment Entry"):
			with self.subTest(doctype=doctype):
				own = self.row(doctype, party_type="Customer", party=self.own.name, docstatus=1)
				wrong_type = self.row(doctype, party_type="Supplier", party=self.own.name, docstatus=1)
				foreign = self.row(doctype, party_type="Customer", party=self.foreign.name, docstatus=1)
				self.assertIn(own.name, self.visible(doctype))
				self.assertNotIn(wrong_type.name, self.visible(doctype))
				self.assertNotIn(foreign.name, self.visible(doctype))
		for doctype, field in (("Quotation", "quotation_to"), ("Opportunity", "opportunity_from")):
			with self.subTest(doctype=doctype):
				own = self.row(doctype, party_name=self.own.name, **{field: "Customer"})
				wrong_type = self.row(doctype, party_name=self.own.name, **{field: "Lead"})
				self.assertIn(own.name, self.visible(doctype))
				self.assertNotIn(wrong_type.name, self.visible(doctype))
		for party in (self.own, self.foreign):
			delivery = self.row("Delivery Note", customer=party.name)
			slip = self.row("Packing Slip", delivery_note=delivery.name)
			self.assertEqual(slip.name in self.visible("Packing Slip"), party.name == self.own.name)

	def test_contacts_and_addresses_require_explicit_authorized_links(self):
		foreign_contact = self.link(self.user, "Customer", self.foreign.name)
		foreign_contact.set("email_ids", [])
		foreign_contact.save()
		for contact in (self.contact, foreign_contact):
			contact.append("phone_nos", {"phone": "5550100000", "is_primary_phone": 1})
			contact.save()
		self.assertIn(self.contact.name, self.visible("Contact"))
		self.assertNotIn(foreign_contact.name, self.visible("Contact"))
		for party in (self.own, self.foreign):
			address = self.row("Address", address_title="Fictional Scope Address")
			self.row("Dynamic Link", parent=address.name, parenttype="Address", parentfield="links",
				link_doctype="Customer", link_name=party.name)
			self.assertEqual(address.name in self.visible("Address"), party.name == self.own.name)

	def test_company_and_account_select_scope_requires_customer_defaults(self):
		for party in (self.own, self.foreign):
			company = self.row("Company", company_name="Fictional Company " + frappe.generate_hash(length=10))
			account = self.row("Account", company=company.name,
				account_name="Fictional Account " + frappe.generate_hash(length=10))
			self.row("Party Account", parent=party.name, parenttype="Customer", parentfield="accounts", company=company.name)
			self.row("GL Entry", party_type="Customer", party=party.name,
				account=account.name, company=company.name, docstatus=1)
			self.assertEqual(company.name in self.visible("Company"), party.name == self.own.name)
			self.assertEqual(account.name in self.visible("Account"), party.name == self.own.name)

	def test_multiple_retailer_memberships_never_grant_upstream_customer_ledgers(self):
		user = self.make_user("YRP Partner", "YRP Customer")
		retailers = [self.row("YRP Retailer", customer=party.name) for party in (self.own, self.foreign)]
		for retailer in retailers:
			self.link(user, "YRP Retailer", retailer.name)
		self.assertEqual(access.retailer_names(user.name), {retailer.name for retailer in retailers})
		self.assertEqual(access.customer_names(user.name), set())
		for retailer in retailers:
			order = self.row("YRP Retail Order", customer=retailer.customer, retailer=retailer.name)
			invoice = self.row("Sales Invoice", customer=retailer.customer)
			self.assertIn(order.name, self.visible("YRP Retail Order", user.name))
			self.assertNotIn(invoice.name, self.visible("Sales Invoice", user.name))
		# Base Partner users retain all their direct retailer memberships too.
		frappe.db.delete("Has Role", {"parent": user.name, "role": "YRP Customer"})
		frappe.clear_cache(user=user.name)
		self.assertEqual(access.retailer_names(user.name), {retailer.name for retailer in retailers})

	def test_customer_membership_does_not_grant_sales_actions(self):
		self.user.append("roles", {"role": "Sales Manager"})
		self.user.append("roles", {"role": "YRP Sales Partner"})
		self.user.save()
		frappe.set_user(self.user.name)
		for doctype in ("Sales Order", "Sales Invoice", "Payment Entry", "GL Entry", "Opportunity", "YRP Retail Order"):
			with self.subTest(doctype=doctype):
				for event in ("before_validate", "before_submit", "before_cancel", "on_trash"):
					with self.assertRaises(frappe.PermissionError):
						prevent_partner_write(frappe.new_doc(doctype), event)

	def test_customer_role_preserves_explicit_sales_person_actions(self):
		person = sales_person(self.own)
		self.user.append("roles", {"role": "YRP Sales Person"})
		self.user.save()
		source_contact = self.link(self.user, "Sales Person", person.name)
		frappe.set_user(self.user.name)
		self.assertTrue(frappe.has_permission("Contact", "read", source_contact))
		visit = frappe.get_doc({"doctype": "YRP Visit", "visit_type": "Primary",
			"sales_person": person.name, "customer": self.own.name,
			"location": '{"type":"Point","coordinates":[77.5,12.5]}'}).insert()
		visit.notes = "Fictional customer role keeps authorized visit editing"
		visit.save()
		self.assertEqual(visit.reload().notes, "Fictional customer role keeps authorized visit editing")
		with self.assertRaises(frappe.PermissionError):
			prevent_partner_write(frappe.new_doc("Sales Invoice"), "before_validate")

	def test_administrators_bypass_customer_restrictions(self):
		user = self.make_user("YRP Partner", "YRP Customer", "System Manager")
		frappe.set_user(user.name)
		self.assertFalse(access.is_customer_user())
		prevent_partner_write(frappe.new_doc("Sales Invoice"))
		frappe.set_user("Administrator")
		self.assertFalse(access.is_customer_report_user())
		self.assertEqual(access.customer_names(), set())

	def test_metadata_discovery_includes_customer_links_but_excludes_children(self):
		self.assertIn(("customer", None), access.customer_links("Sales Invoice"))
		self.assertIn(("party_name", "opportunity_from"), access.customer_links("Opportunity"))
		self.assertEqual(access.customer_links("Sales Invoice Item"), ())
		self.assertEqual(access.customer_links("Activity Log"), ())
		self.assertEqual(access.customer_links("Comment"), ())
		self.assertEqual(access.customer_query_condition("Currency", self.user.name), "1=0")

	def test_calendar_uses_customer_scope_even_with_empty_client_filters(self):
		from frappe.utils import today
		from yrp.yrp_partner.customer_api import sales_order_events
		orders = []
		for party in (self.own, self.foreign):
			order = self.row("Sales Order", customer=party.name, customer_name=party.customer_name,
				skip_delivery_note=0)
			self.row("Sales Order Item", parent=order.name, parenttype="Sales Order", parentfield="items",
				delivery_date=today())
			orders.append(order)
		frappe.set_user(self.user.name)
		rows = sales_order_events(today(), today(), [])
		self.assertEqual({row.name for row in rows}, {orders[0].name})

	def test_official_company_addresses_follow_company_access_without_customer_expansion(self):
		addresses = []
		for party in (self.own, self.foreign):
			company = self.row("Company", company_name="Fictional Official Company " + frappe.generate_hash(length=8))
			self.row("Party Account", parent=party.name, parenttype="Customer", parentfield="accounts", company=company.name)
			self.row("Sales Invoice", customer=party.name, company=company.name)
			address = self.row("Address", address_title="Fictional Official Address")
			self.row("Dynamic Link", parent=address.name, parenttype="Address", parentfield="links",
				link_doctype="Company", link_name=company.name)
			addresses.append(address)
		foreign_customer_address = self.row("Address", address_title="Fictional Other Customer Address")
		self.row("Dynamic Link", parent=foreign_customer_address.name, parenttype="Address", parentfield="links",
			link_doctype="Customer", link_name=self.foreign.name)
		frappe.set_user(self.user.name)
		self.assertTrue(frappe.has_permission("Address", "read", addresses[0]))
		self.assertFalse(frappe.has_permission("Address", "read", addresses[1]))
		frappe.set_user("Administrator")
		person = sales_person(self.own)
		self.user.append("roles", {"role": "YRP Sales Person"})
		self.user.save()
		self.link(self.user, "Sales Person", person.name)
		frappe.set_user(self.user.name)
		self.assertFalse(frappe.has_permission("Address", "read", addresses[1]))
		self.assertFalse(frappe.has_permission("Address", "read", foreign_customer_address))
		self.assertEqual(access.customer_names(), {self.own.name})

	def test_sidebar_reports_include_native_routing_metadata(self):
		from yrp.yrp_partner.workspace import SIDEBAR, add_partner_navigation
		from yrp.yrp_partner.customer_reports import CUSTOMER_REPORTS
		frappe.set_user(self.user.name)
		boot = frappe._dict(workspace_sidebar_item={SIDEBAR.lower(): {"items": []}})
		add_partner_navigation(boot)
		reports = {row.link_to: row for row in boot.workspace_sidebar_item[SIDEBAR.lower()]["items"]
			if row.link_type == "Report"}
		self.assertEqual(set(reports), set(CUSTOMER_REPORTS))
		for name, link in reports.items():
			self.assertEqual(link.report["report_type"], "Script Report")
			self.assertEqual(link.report["ref_doctype"], CUSTOMER_REPORTS[name])
