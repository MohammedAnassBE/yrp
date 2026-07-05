# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestWhatsAppHooks(IntegrationTestCase):
	def test_daily_scheduler_registers_template_sync(self):
		"""The daily scheduler must call the Task 07 hub template-sync endpoint."""
		daily = frappe.get_hooks("scheduler_events").get("daily") or []
		self.assertIn("yrp.whatsapp_templates.sync_templates_from_hub", daily)

	def test_property_setter_registered_as_fixture(self):
		"""Property Setter must be exportable so the WhatsApp medium is never dropped."""
		fixtures = frappe.get_hooks("fixtures", app_name="yrp")
		registered = any(
			isinstance(f, dict) and (f.get("dt") or f.get("doctype")) == "Property Setter"
			for f in fixtures
		)
		self.assertTrue(registered, "Property Setter not registered in yrp fixtures hook")

	def test_communication_medium_includes_whatsapp(self):
		"""Task 09's after_migrate hook (add_whatsapp_communication_medium) must add WhatsApp on migrate."""
		field = frappe.get_meta("Communication").get_field("communication_medium")
		options = (field.options or "").split("\n")
		self.assertIn("WhatsApp", options)
