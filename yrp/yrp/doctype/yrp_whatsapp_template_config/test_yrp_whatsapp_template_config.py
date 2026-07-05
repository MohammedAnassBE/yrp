# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

from frappe.tests import IntegrationTestCase

from yrp.yrp.doctype.yrp_whatsapp_hub_settings.yrp_whatsapp_hub_settings import (
	parse_whatsapp_variables,
)

# NOTE: YRP WhatsApp Template Config (the per-doctype template routing row this
# folder is named after) and YRP WhatsApp Hub Settings.get_whatsapp_config()
# were retired by the template-centric + settings-allowlist refactor -- see
# YRP WhatsApp Hub Settings.enabled_doctypes (YRP WhatsApp Enabled DocType) and
# YRP WhatsApp Template.applicable_doctypes (YRP WhatsApp Template DocType).
# parse_whatsapp_variables is still used (yrp/whatsapp_notification.py), so its
# coverage stays here.


class TestParseWhatsAppVariables(IntegrationTestCase):
	def test_parse_orders_positional_distinct(self):
		self.assertEqual(
			parse_whatsapp_variables("Hi {{1}}, order {{2}} for {{1}} at {{3}}"),
			[1, 2, 3],
		)

	def test_parse_first_seen_order(self):
		self.assertEqual(parse_whatsapp_variables("{{2}} then {{1}} then {{ 2 }}"), [2, 1])

	def test_parse_empty(self):
		self.assertEqual(parse_whatsapp_variables(""), [])
		self.assertEqual(parse_whatsapp_variables(None), [])
