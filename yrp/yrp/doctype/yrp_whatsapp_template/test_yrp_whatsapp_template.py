# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestYRPWhatsAppTemplate(IntegrationTestCase):
	def _ensure_account(self):
		"""Return a YRP WhatsApp Account name, creating one if none exists."""
		name = "yrp-wa-test-acct"
		if not frappe.db.exists("YRP WhatsApp Account", name):
			frappe.get_doc(
				{
					"doctype": "YRP WhatsApp Account",
					"account_name": name,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		return name

	def _make_template(self, template_name, status="DRAFT", from_meta_sync=True):
		fq = f"{template_name}-en"
		if frappe.db.exists("YRP WhatsApp Template", fq):
			frappe.delete_doc(
				"YRP WhatsApp Template", fq, ignore_permissions=True, force=True
			)
		doc = frappe.get_doc(
			{
				"doctype": "YRP WhatsApp Template",
				"template_name": template_name,
				"language_code": "en",
				"category": "UTILITY",
				"status": status,
				"whatsapp_account": self._ensure_account(),
				"body_text": "Order {{1}} is ready for {{2}}.",
			}
		)
		if from_meta_sync:
			doc.flags.from_meta_sync = True
		doc.insert(ignore_permissions=True)
		return doc

	def test_template_creates_with_children(self):
		doc = self._make_template("yrp_wa_create")
		doc.append(
			"buttons", {"button_type": "QUICK_REPLY", "button_text": "Confirm"}
		)
		doc.append(
			"sample_values",
			{"variable_number": 1, "variable_type": "body", "sample_value": "PO-0001"},
		)
		doc.flags.from_meta_sync = True
		doc.save(ignore_permissions=True)

		self.assertTrue(
			frappe.db.exists("YRP WhatsApp Template", "yrp_wa_create-en")
		)
		reloaded = frappe.get_doc("YRP WhatsApp Template", "yrp_wa_create-en")
		# format:{template_name}-{language_code} autoname
		self.assertEqual(reloaded.name, "yrp_wa_create-en")
		self.assertEqual(len(reloaded.buttons), 1)
		self.assertEqual(len(reloaded.sample_values), 1)
		self.assertEqual(reloaded.buttons[0].button_type, "QUICK_REPLY")
		self.assertEqual(reloaded.sample_values[0].variable_type, "body")

	def test_from_meta_sync_guard_preserves_status(self):
		# from_meta_sync True → Meta's real status (APPROVED) is kept as-is
		synced = self._make_template(
			"yrp_wa_synced", status="APPROVED", from_meta_sync=True
		)
		self.assertEqual(synced.status, "APPROVED")
		# from_meta_sync False → validate() forces a hand-created doc to DRAFT
		manual = self._make_template(
			"yrp_wa_manual", status="APPROVED", from_meta_sync=False
		)
		self.assertEqual(manual.status, "DRAFT")

	def test_approved_filter_via_get_all(self):
		self._make_template(
			"yrp_wa_approved", status="APPROVED", from_meta_sync=True
		)
		self._make_template("yrp_wa_draft", status="DRAFT", from_meta_sync=True)

		approved = frappe.get_all(
			"YRP WhatsApp Template",
			filters={"status": "APPROVED", "template_name": ["like", "yrp_wa_%"]},
			pluck="name",
		)
		self.assertIn("yrp_wa_approved-en", approved)
		self.assertNotIn("yrp_wa_draft-en", approved)

	def test_is_applicable_for_reads_applicable_doctypes(self):
		doc = self._make_template("yrp_wa_applic", status="APPROVED")
		doc.append("applicable_doctypes", {"reference_doctype": "Purchase Order"})
		doc.flags.from_meta_sync = True
		doc.save(ignore_permissions=True)

		self.assertTrue(doc.is_applicable_for("Purchase Order"))
		self.assertFalse(doc.is_applicable_for("Stock Entry"))

		reloaded = frappe.get_doc("YRP WhatsApp Template", doc.name)
		self.assertTrue(reloaded.is_applicable_for("Purchase Order"))
		self.assertFalse(reloaded.is_applicable_for("Delivery Challan"))
