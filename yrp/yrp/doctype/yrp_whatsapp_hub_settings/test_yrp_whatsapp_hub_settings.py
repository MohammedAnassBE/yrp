# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestYRPWhatsAppHubSettings(IntegrationTestCase):
	def setUp(self):
		self.settings = frappe.get_single("YRP WhatsApp Hub Settings")
		self.settings.hub_url = "https://hub.example.com/"
		self.settings.api_key = "testapikey123"
		self.settings.api_secret = "testapisecret456"
		self.settings.enabled = 1
		self.settings.accounts = []
		self.settings.append("accounts", {"account_name": "MainAccount", "is_default": 1})
		self.settings.append("accounts", {"account_name": "SecondaryAccount", "is_default": 0})

	def test_get_hub_url_strips_trailing_slash(self):
		"""get_hub_url should strip the trailing slash from hub_url."""
		result = self.settings.get_hub_url()
		self.assertEqual(result, "https://hub.example.com")
		self.assertFalse(result.endswith("/"))

	def test_get_default_account_name(self):
		"""get_default_account_name returns the account marked is_default."""
		self.assertEqual(self.settings.get_default_account_name(), "MainAccount")

	def test_get_account_name_with_specific_name(self):
		"""get_account_name with a specific name returns that account name."""
		self.assertEqual(
			self.settings.get_account_name(account_name="SecondaryAccount"),
			"SecondaryAccount",
		)

	def test_get_account_name_with_nonexistent_name_raises(self):
		"""get_account_name with an unknown account name raises a ValidationError."""
		with self.assertRaises(frappe.exceptions.ValidationError):
			self.settings.get_account_name(account_name="NonExistentAccount")

	def test_get_hub_auth_headers_builds_token(self):
		"""get_hub_auth_headers builds the `token key:secret` Authorization header."""
		# Password fields are read back from storage, so persist first.
		self.settings.save(ignore_permissions=True)
		headers = self.settings.get_hub_auth_headers()
		self.assertEqual(headers["Authorization"], "token testapikey123:testapisecret456")
		self.assertEqual(headers["Content-Type"], "application/json")


class TestYRPWhatsAppHubSettingsEnabledDoctypes(IntegrationTestCase):
	"""The settings-allowlist pattern (mirrors YRP E-Waybill Settings /
	YRP EWB Enabled DocType): enabled_doctypes gates WHICH doctypes may send a
	WhatsApp message at all; per-template applicability is a separate concern
	on YRP WhatsApp Template itself."""

	def setUp(self):
		self.settings = frappe.get_single("YRP WhatsApp Hub Settings")
		self.settings.set("enabled_doctypes", [])
		self.settings.append("enabled_doctypes", {
			"reference_doctype": "Purchase Order", "enabled": 1, "supplier_key": "supplier",
		})
		self.settings.append("enabled_doctypes", {
			"reference_doctype": "Stock Entry", "enabled": 1, "supplier_key": "to_supplier",
		})
		self.settings.append("enabled_doctypes", {
			"reference_doctype": "Delivery Challan", "enabled": 0, "supplier_key": "supplier",
		})
		self.settings.save(ignore_permissions=True)

	def test_get_enabled_doctypes_lists_only_enabled_rows(self):
		enabled = self.settings.get_enabled_doctypes()
		self.assertIn("Purchase Order", enabled)
		self.assertIn("Stock Entry", enabled)
		self.assertNotIn("Delivery Challan", enabled)  # enabled=0

	def test_is_doctype_enabled(self):
		self.assertTrue(self.settings.is_doctype_enabled("Purchase Order"))
		self.assertFalse(self.settings.is_doctype_enabled("Delivery Challan"))
		self.assertFalse(self.settings.is_doctype_enabled("Warehouse"))  # not listed at all

	def test_get_supplier_key_returns_row_value_or_default(self):
		self.assertEqual(self.settings.get_supplier_key("Stock Entry"), "to_supplier")
		self.assertEqual(self.settings.get_supplier_key("Purchase Order"), "supplier")
		# a doctype with no row at all still gets the "supplier" default
		self.assertEqual(self.settings.get_supplier_key("Warehouse"), "supplier")


class TestYRPHubSettingsAccountAutosync(IntegrationTestCase):
	TEST_ACCT = "TestAutosyncAcct"

	def setUp(self):
		settings = frappe.get_single("YRP WhatsApp Hub Settings")
		if not settings.hub_url:
			settings.hub_url = "https://hub.test"
		if not settings.api_key:
			settings.api_key = "k"
		if not settings.api_secret:
			settings.api_secret = "s"
		settings.save(ignore_permissions=True)

	def tearDown(self):
		if frappe.db.exists("YRP WhatsApp Account", self.TEST_ACCT):
			frappe.delete_doc("YRP WhatsApp Account", self.TEST_ACCT,
							  ignore_permissions=True, force=True)
		settings = frappe.get_single("YRP WhatsApp Hub Settings")
		settings.set("accounts",
					 [r for r in (settings.get("accounts") or [])
					  if (r.account_name or "") != self.TEST_ACCT])
		settings.save(ignore_permissions=True)

	def test_on_update_creates_missing_whatsapp_account(self):
		settings = frappe.get_single("YRP WhatsApp Hub Settings")
		settings.set("accounts",
					 [r for r in (settings.get("accounts") or [])
					  if (r.account_name or "") != self.TEST_ACCT])
		settings.append("accounts", {"account_name": self.TEST_ACCT, "is_default": 1})
		settings.save(ignore_permissions=True)

		self.assertTrue(
			frappe.db.exists("YRP WhatsApp Account", self.TEST_ACCT),
			"YRP WhatsApp Account should be auto-created from Hub Settings child row",
		)
		acct = frappe.get_doc("YRP WhatsApp Account", self.TEST_ACCT)
		self.assertEqual(int(acct.is_default), 1)

	def test_on_update_updates_existing_whatsapp_account(self):
		if not frappe.db.exists("YRP WhatsApp Account", self.TEST_ACCT):
			frappe.get_doc({
				"doctype": "YRP WhatsApp Account",
				"account_name": self.TEST_ACCT,
				"is_default": 0,
			}).insert(ignore_permissions=True, ignore_mandatory=True)

		settings = frappe.get_single("YRP WhatsApp Hub Settings")
		settings.set("accounts",
					 [r for r in (settings.get("accounts") or [])
					  if (r.account_name or "") != self.TEST_ACCT])
		settings.append("accounts", {"account_name": self.TEST_ACCT, "is_default": 1})
		settings.save(ignore_permissions=True)

		self.assertEqual(
			int(frappe.db.get_value("YRP WhatsApp Account", self.TEST_ACCT, "is_default")),
			1,
		)
