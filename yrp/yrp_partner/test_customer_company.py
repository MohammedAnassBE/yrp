"""Company scope follows live native Customer Default Accounts assignments.

All records are fictional and every test rolls back. Identity fixtures retain
their ordinary hooks; synthetic accounting rows exercise authorization without
posting transactions or changing site-wide defaults.
"""

import unittest

import frappe

from yrp.yrp_partner import customer_access as access
from yrp.yrp_partner import test_customer_access as access_fixtures
from yrp.yrp_partner.permissions import prevent_partner_write, query_conditions
from yrp.yrp_partner.sales_roles import allowed_action
from yrp.yrp_retail.tests.fixtures import sales_person


class TestCustomerCompany(unittest.TestCase):
	# Reuse fixture methods by composition, without collecting unrelated tests.
	make_user = access_fixtures.TestCustomerAccess.make_user
	link = access_fixtures.TestCustomerAccess.link
	row = access_fixtures.TestCustomerAccess.row
	visible = access_fixtures.TestCustomerAccess.visible

	def setUp(self):
		access_fixtures.TestCustomerAccess.setUp(self)
		frappe.db.delete("Party Account", {
			"parenttype": "Customer", "parent": ["in", [self.own.name, self.foreign.name]],
		})
		self.companies = [self.row("Company",
			company_name="Fictional Company Scope " + frappe.generate_hash(length=12),
			default_currency="INR", is_group=0) for _ in range(3)]
		self.company_names = {company.name for company in self.companies}

	def configure(self, customer, company, **values):
		return self.row("Party Account", parent=customer.name,
			parenttype=values.pop("parenttype", "Customer"),
			parentfield=values.pop("parentfield", "accounts"),
			company=company.name, **values)

	def actors(self, *customers):
		"""Give each business role the same live Customer membership union."""
		users = {}
		for role, source in (("YRP Customer", "Customer"),
			("YRP Sales Person", "Sales Person"), ("YRP Sales Partner", "Sales Partner")):
			user = self.make_user("YRP Partner", role)
			for customer in customers:
				reference = customer.name
				if source == "Sales Person":
					reference = sales_person(customer).name
				elif source == "Sales Partner":
					reference = customer.default_sales_partner
				self.link(user, source, reference)
			users[role] = user
		return users

	def company_surface(self, user, expected):
		from frappe.desk.search import search_link

		frappe.set_user(user.name)
		self.assertEqual(access.company_names(), expected)
		self.assertEqual(set(frappe.get_list("Company", filters={
			"name": ["in", sorted(self.company_names)]}, pluck="name")), expected)
		self.assertEqual({row["value"] for row in search_link("Company", "", filters={
			"name": ["in", sorted(self.company_names)]}, page_length=50)}, expected)
		for company in self.companies:
			self.assertEqual(frappe.has_permission("Company", "read", company), company.name in expected)
			if company.name not in expected:
				with self.assertRaises(frappe.PermissionError):
					company.check_permission("read")

	def test_company_lists_reads_and_link_search_use_defaults_before_first_sale_for_every_role(self):
		self.configure(self.own, self.companies[0])
		self.configure(self.foreign, self.companies[1])
		users = self.actors(self.own)
		for role, user in users.items():
			with self.subTest(role=role):
				self.company_surface(user, {self.companies[0].name})
				self.assertEqual(access.customer_names(company=self.companies[0].name), {self.own.name})
				self.assertEqual(access.customer_names(company=self.companies[1].name), set())

	def test_multiple_customers_and_contact_sources_union_companies_for_every_role(self):
		for customer, companies in ((self.own, self.companies[:2]),
			(self.foreign, self.companies[1:])):
			for company in companies:
				self.configure(customer, company)
		users = self.actors(self.own, self.foreign)
		for role, user in users.items():
			with self.subTest(role=role):
				self.assertEqual(access.company_names(user.name), self.company_names)
				self.assertEqual(access.company_names(user.name, customers={self.own.name}),
					{company.name for company in self.companies[:2]})
				self.assertEqual(access.company_names(user.name, customers=set()), set())
				self.assertEqual(access.customer_names(user.name, company=self.companies[1].name),
					{self.own.name, self.foreign.name})
				self.assertEqual(access.customer_names(user.name, company=self.companies[2].name),
					{self.foreign.name})

	def test_customer_subset_cannot_grant_a_foreign_customer_company(self):
		self.configure(self.own, self.companies[0])
		self.configure(self.foreign, self.companies[1])
		self.assertEqual(access.company_names(self.user.name, customers={self.foreign.name}), set())
		self.assertEqual(access.company_names(self.user.name,
			customers={self.own.name, self.foreign.name}), {self.companies[0].name})

	def test_no_defaults_means_no_companies_despite_user_defaults_and_transaction_history(self):
		for doctype in ("Sales Order", "Sales Invoice", "Delivery Note", "Quotation", "GL Entry"):
			self.row(doctype, customer=self.own.name, party_name=self.own.name,
				quotation_to="Customer", party_type="Customer", party=self.own.name,
				company=self.companies[0].name, docstatus=1)
		users = self.actors(self.own)
		for role, user in users.items():
			with self.subTest(role=role):
				frappe.defaults.set_user_default("company", self.companies[0].name, user=user.name)
				self.addCleanup(frappe.defaults.clear_defaults_cache, user.name)
				self.assertEqual(frappe.defaults.get_user_default("company", user=user.name),
					self.companies[0].name)
				self.company_surface(user, set())
				self.assertEqual(access.customer_names(), {self.own.name})

	def test_account_update_and_removal_revoke_company_and_document_access_immediately(self):
		assignment = self.configure(self.own, self.companies[0])
		invoice = self.row("Sales Invoice", customer=self.own.name, company=self.companies[0].name)
		self.company_surface(self.user, {self.companies[0].name})
		self.assertTrue(frappe.has_permission("Sales Invoice", "read", invoice))
		frappe.set_user("Administrator")
		frappe.db.set_value("Party Account", assignment.name, "company", self.companies[1].name)
		self.company_surface(self.user, {self.companies[1].name})
		self.assertFalse(frappe.has_permission("Sales Invoice", "read", invoice))
		frappe.set_user("Administrator")
		frappe.db.delete("Party Account", {"name": assignment.name})
		self.company_surface(self.user, set())
		self.assertEqual(access.customer_names(company=self.companies[1].name), set())

	def test_only_native_customer_accounts_rows_grant_company_access(self):
		self.configure(self.own, self.companies[0], parenttype="Supplier")
		self.configure(self.own, self.companies[1], parentfield="credit_limits")
		self.configure(self.foreign, self.companies[2])
		self.company_surface(self.user, set())
		frappe.set_user("Administrator")
		self.configure(self.own, self.companies[0])
		self.company_surface(self.user, {self.companies[0].name})

	def test_parent_and_child_companies_are_not_implicitly_authorized(self):
		frappe.db.set_value("Company", self.companies[0].name, "parent_company", self.companies[1].name)
		frappe.db.set_value("Company", self.companies[2].name, "parent_company", self.companies[0].name)
		self.configure(self.own, self.companies[0])
		for role, user in self.actors(self.own).items():
			with self.subTest(role=role):
				self.company_surface(user, {self.companies[0].name})

	def test_official_company_addresses_follow_the_same_scope_for_every_role(self):
		self.configure(self.own, self.companies[0])
		addresses = []
		for company in self.companies:
			address = self.row("Address", address_title="Fictional Company Scope Address")
			self.row("Dynamic Link", parent=address.name, parenttype="Address", parentfield="links",
				link_doctype="Company", link_name=company.name)
			addresses.append(address)
		foreign_address = self.row("Address", address_title="Fictional Foreign Customer Address")
		self.row("Dynamic Link", parent=foreign_address.name, parenttype="Address", parentfield="links",
			link_doctype="Customer", link_name=self.foreign.name)
		addresses.append(foreign_address)
		for role, user in self.actors(self.own).items():
			with self.subTest(role=role):
				frappe.set_user(user.name)
				self.assertEqual(set(frappe.get_list("Address", filters={
					"name": ["in", [address.name for address in addresses]]}, pluck="name")),
					{addresses[0].name})
				for index, address in enumerate(addresses):
					self.assertEqual(frappe.has_permission("Address", "read", address), index == 0)

	def test_all_roles_read_only_related_accounts_in_configured_companies(self):
		from frappe.desk.search import search_link

		accounts = [self.row("Account", company=company.name, is_group=0,
			account_name="Fictional Select Account " + frappe.generate_hash(length=10))
			for company in self.companies]
		self.configure(self.own, self.companies[0], account=accounts[0].name)
		unrelated = self.row("Account", company=self.companies[0].name,
			account_name="Fictional Unrelated Expense", is_group=0)
		accounts.append(unrelated)
		for role, user in self.actors(self.own).items():
			frappe.set_user(user.name)
			with self.subTest(role=role):
				self.assertEqual({row["value"] for row in search_link("Account", "", filters={
					"name": ["in", [account.name for account in accounts]]}, page_length=50)}, {accounts[0].name})
				for index, account in enumerate(accounts):
					for action in ("read", "select"):
						self.assertEqual(frappe.has_permission("Account", action, account), index == 0)
					for action in ("write", "create", "delete"):
						self.assertFalse(frappe.has_permission("Account", action, account))

	def test_account_defaults_and_customer_ledger_accounts_do_not_grant_other_company_accounts(self):
		accounts = [self.row("Account", company=self.companies[0].name,
			account_name="Fictional Receivable " + frappe.generate_hash(length=10), is_group=0) for _ in range(4)]
		self.configure(self.own, self.companies[0], advance_account=accounts[1].name)
		frappe.db.set_value("Company", self.companies[0].name, "default_receivable_account", accounts[0].name)
		self.row("GL Entry", party_type="Customer", party=self.own.name,
			company=self.companies[0].name, account=accounts[2].name)
		self.row("GL Entry", party_type="Customer", party=self.foreign.name,
			company=self.companies[0].name, account=accounts[3].name)
		self.assertEqual(self.visible("Account"), {account.name for account in accounts[:3]})

	def test_native_group_account_fallback_requires_explicit_customer_company_assignment(self):
		accounts = [self.row("Account", company=self.companies[0].name,
			account_name="Fictional Group Account " + frappe.generate_hash(length=10), is_group=0) for _ in range(3)]
		frappe.db.set_value("Company", self.companies[0].name, "default_receivable_account", accounts[2].name)
		self.row("Party Account", parent=self.own.customer_group, parenttype="Customer Group",
			parentfield="accounts", company=self.companies[0].name,
			account=accounts[0].name, advance_account=accounts[1].name)
		self.assertEqual(self.visible("Account"), set())
		self.assertEqual(access.company_names(self.user.name), set())
		self.configure(self.own, self.companies[0])
		self.assertEqual(self.visible("Account"), {a.name for a in accounts[:2]})

	def test_native_documents_require_the_exact_customer_company_pair_for_every_role(self):
		self.configure(self.own, self.companies[0])
		self.configure(self.foreign, self.companies[1])
		users = self.actors(self.own, self.foreign)
		for user in users.values():
			# Supply Opportunity's native read grant; additive roles must still
			# respect the same exact Customer/company authorization boundary.
			user.append("roles", {"role": "Sales User"})
			user.save()
		for doctype in ("Sales Order", "Sales Invoice", "Delivery Note", "Quotation", "Opportunity"):
			documents = []
			frappe.set_user("Administrator")
			for customer, company in ((self.own, self.companies[0]), (self.foreign, self.companies[1]),
				(self.own, self.companies[1]), (self.foreign, self.companies[0]),
				(self.own, self.companies[2]), (self.own, None)):
				documents.append(self.row(doctype, customer=customer.name, party_name=customer.name,
					quotation_to="Customer", opportunity_from="Customer",
					company=company.name if company else ""))
			allowed = {document.name for document in documents[:2]}
			for role, user in users.items():
				with self.subTest(doctype=doctype, role=role):
					frappe.set_user(user.name)
					self.assertEqual(set(frappe.get_list(doctype, filters={
						"name": ["in", [document.name for document in documents]]}, pluck="name")), allowed)
					for document in documents:
						self.assertEqual(frappe.has_permission(doctype, "read", document), document.name in allowed)

	def test_draft_sales_order_actions_check_stored_and_incoming_customer_company_pairs(self):
		assignment = self.configure(self.own, self.companies[0])
		self.configure(self.foreign, self.companies[1])
		user = self.make_user("YRP Partner", "YRP Sales Partner")
		for customer in (self.own, self.foreign):
			self.link(user, "Sales Partner", customer.default_sales_partner)
		stored = self.row("Sales Order", customer=self.own.name, company=self.companies[0].name)
		wrong_stored = self.row("Sales Order", customer=self.own.name, company=self.companies[1].name)
		frappe.set_user(user.name)
		valid = frappe.get_doc({"doctype": "Sales Order", "__islocal": 1, "customer": self.own.name,
			"company": self.companies[0].name})
		self.assertTrue(allowed_action(valid, "create"))
		prevent_partner_write(valid, "before_validate")
		valid.company = self.companies[1].name
		self.assertFalse(allowed_action(valid, "create"))
		with self.assertRaises(frappe.PermissionError):
			prevent_partner_write(valid, "before_validate")
		stored.company = self.companies[1].name
		self.assertFalse(allowed_action(stored, "write"))
		wrong_stored.company = self.companies[0].name
		self.assertFalse(allowed_action(wrong_stored, "write"))
		stored.reload()
		self.assertTrue(allowed_action(stored, "write"))
		frappe.set_user("Administrator")
		frappe.db.delete("Party Account", {"name": assignment.name})
		frappe.set_user(user.name)
		self.assertFalse(allowed_action(stored, "write"))

	def test_administrators_keep_native_company_access_without_customer_defaults(self):
		manager = self.make_user("YRP Partner", "YRP Customer", "System Manager")
		for user in ("Administrator", manager.name):
			with self.subTest(user=user):
				frappe.set_user(user)
				self.assertEqual(query_conditions(user=user, doctype="Company"), "")
				self.assertEqual(set(frappe.get_list("Company", filters={
					"name": ["in", sorted(self.company_names)]}, pluck="name")), self.company_names)
				for company in self.companies:
					self.assertTrue(frappe.has_permission("Company", "read", company))

	def test_boot_company_payload_and_ui_defaults_follow_assignments_without_persisting(self):
		from yrp.yrp_partner.workspace import scope_company_bootinfo

		assignment = self.configure(self.own, self.companies[0])
		frappe.set_user(self.user.name)
		defaults = {"company": self.companies[1].name, "currency": "INR"}
		boot = frappe._dict(user=frappe._dict(defaults=defaults), sysdefaults=defaults,
			docs=[dict(doctype=":Company", name=c.name) for c in self.companies] + [dict(doctype="Currency", name="INR")])
		scope_company_bootinfo(boot)
		self.assertEqual([d['name'] for d in boot.docs if d['doctype'] == ':Company'], [self.companies[0].name])
		self.assertEqual(boot.user.defaults['company'], self.companies[0].name)
		self.assertEqual(boot.sysdefaults.company, self.companies[0].name)
		self.assertEqual(defaults['company'], self.companies[1].name)
		self.assertIn(dict(doctype="Currency", name="INR"), boot.docs)
		frappe.set_user("Administrator")
		frappe.db.delete("Party Account", {"name": assignment.name})
		frappe.set_user(self.user.name)
		scope_company_bootinfo(boot)
		self.assertIsNone(boot.user.defaults['company'])
		self.assertFalse(any(d['doctype'] == ':Company' for d in boot.docs))
