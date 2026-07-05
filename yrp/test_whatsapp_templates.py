# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from yrp import whatsapp_templates


class TestWhatsAppTemplates(IntegrationTestCase):
    def _ensure_account(self):
        """Return a YRP WhatsApp Account name, creating one if none exists.
        The local account_name equals the hub account_name in a single-account
        spoke, so it doubles as the whatsapp_account link target here."""
        name = "yrp-wa-tmpl-acct"
        if not frappe.db.exists("YRP WhatsApp Account", name):
            frappe.get_doc(
                {
                    "doctype": "YRP WhatsApp Account",
                    "account_name": name,
                    "enabled": 1,
                }
            ).insert(ignore_permissions=True)
        return name

    def _meta_template(self, template_id, name, status, body, header=None, footer=None):
        """Build a Meta-shape template dict as the hub's sync payload returns it."""
        components = [{"type": "BODY", "text": body}]
        if header:
            components.append({"type": "HEADER", "format": "TEXT", "text": header})
        if footer:
            components.append({"type": "FOOTER", "text": footer})
        return {
            "id": template_id,
            "name": name,
            "language": "en",
            "category": "UTILITY",
            "status": status,
            "components": components,
        }

    def _drop_by_template_id(self, template_id):
        existing = frappe.db.get_value(
            "YRP WhatsApp Template", {"template_id": template_id}, "name"
        )
        if existing:
            frappe.delete_doc(
                "YRP WhatsApp Template", existing, ignore_permissions=True, force=True
            )

    def test_upsert_creates_then_updates_by_template_id(self):
        account = self._ensure_account()
        self._drop_by_template_id("MTID-100")

        # First upsert with a new template_id -> INSERT
        name1 = whatsapp_templates._upsert_local_template(
            self._meta_template(
                "MTID-100", "yrp_wa_upsert", "APPROVED", "Order {{1}} ready for {{2}}."
            ),
            account,
        )
        self.assertTrue(frappe.db.exists("YRP WhatsApp Template", name1))
        doc1 = frappe.get_doc("YRP WhatsApp Template", name1)
        self.assertEqual(doc1.template_id, "MTID-100")
        # from_meta_sync bypasses the DRAFT-forcing validate guard -> APPROVED kept
        self.assertEqual(doc1.status, "APPROVED")
        self.assertEqual(doc1.body_text, "Order {{1}} ready for {{2}}.")

        # Second upsert, same template_id, changed status + body -> UPDATE same row
        name2 = whatsapp_templates._upsert_local_template(
            self._meta_template(
                "MTID-100", "yrp_wa_upsert", "PAUSED", "Order {{1}} is delayed."
            ),
            account,
        )
        self.assertEqual(name2, name1)  # keyed on template_id, not a fresh row
        doc2 = frappe.get_doc("YRP WhatsApp Template", name2)
        self.assertEqual(doc2.status, "PAUSED")
        self.assertEqual(doc2.body_text, "Order {{1}} is delayed.")
        self.assertEqual(
            frappe.db.count("YRP WhatsApp Template", {"template_id": "MTID-100"}), 1
        )

    def test_upsert_preserves_applicable_doctypes_on_resync(self):
        """applicable_doctypes is pure user config (which enabled DocTypes may
        send this template) -- a Meta re-sync must never clear it, only the
        Meta-sourced fields (body/header/footer/status/buttons/category)."""
        account = self._ensure_account()
        self._drop_by_template_id("MTID-300")

        name = whatsapp_templates._upsert_local_template(
            self._meta_template("MTID-300", "yrp_wa_preserve", "APPROVED", "Hello {{1}}"),
            account,
        )
        doc = frappe.get_doc("YRP WhatsApp Template", name)
        doc.append("applicable_doctypes", {"reference_doctype": "Purchase Order"})
        doc.append("applicable_doctypes", {"reference_doctype": "Stock Entry"})
        doc.flags.from_meta_sync = True
        doc.save(ignore_permissions=True)
        self.assertEqual(len(doc.applicable_doctypes), 2)

        # Re-sync: status + body change, but applicable_doctypes must survive untouched.
        name2 = whatsapp_templates._upsert_local_template(
            self._meta_template(
                "MTID-300", "yrp_wa_preserve", "PAUSED", "Hello {{1}}, updated"
            ),
            account,
        )
        self.assertEqual(name2, name)
        reloaded = frappe.get_doc("YRP WhatsApp Template", name2)
        self.assertEqual(reloaded.status, "PAUSED")
        self.assertEqual(reloaded.body_text, "Hello {{1}}, updated")
        applicable = sorted(r.reference_doctype for r in reloaded.applicable_doctypes)
        self.assertEqual(applicable, ["Purchase Order", "Stock Entry"])

    def test_get_template_variables_body_and_header_sorted(self):
        account = self._ensure_account()
        self._drop_by_template_id("MTID-200")

        name = whatsapp_templates._upsert_local_template(
            self._meta_template(
                "MTID-200",
                "yrp_wa_vars",
                "APPROVED",
                "Hello {{2}}, your order {{1}} ships today.",
                header="Update for {{1}}",
            ),
            account,
        )

        variables = whatsapp_templates.get_template_variables(name)

        # body vars first (ascending by number), then header vars
        self.assertEqual(
            variables,
            [
                {"number": 1, "type": "body", "sample_value": ""},
                {"number": 2, "type": "body", "sample_value": ""},
                {"number": 1, "type": "header", "sample_value": ""},
            ],
        )

    def test_sync_gate_rejects_roleless_user(self):
        # frappe.only_for("System Manager") raises PermissionError for any
        # non-Administrator lacking the role (no in_test short-circuit in v16).
        roleless = "yrp-wa-roleless@example.com"
        if not frappe.db.exists("User", roleless):
            frappe.get_doc(
                {
                    "doctype": "User",
                    "email": roleless,
                    "first_name": "Roleless",
                    "send_welcome_email": 0,
                }
            ).insert(ignore_permissions=True)
        # guarantee the disjoint check triggers
        frappe.db.delete("Has Role", {"parent": roleless, "role": "System Manager"})

        frappe.set_user(roleless)
        try:
            with self.assertRaises(frappe.PermissionError):
                whatsapp_templates.sync_templates_from_hub()
        finally:
            frappe.set_user("Administrator")

    def test_sync_skipped_when_hub_disabled(self):
        # The daily scheduler_events entry hits this same function while the
        # hub is dormant/unconfigured -> must degrade to a benign skip, never
        # frappe.throw (which would write a fresh Error Log every day).
        settings = frappe.get_single("YRP WhatsApp Hub Settings")
        original_enabled = settings.enabled
        settings.enabled = 0
        settings.save(ignore_permissions=True)
        try:
            result = whatsapp_templates.sync_templates_from_hub()
            self.assertEqual(result, {"skipped": True, "reason": "WhatsApp hub disabled"})
        finally:
            settings = frappe.get_single("YRP WhatsApp Hub Settings")
            settings.enabled = original_enabled
            settings.save(ignore_permissions=True)

    def test_normalize_meta_status_maps_the_set(self):
        self.assertEqual(whatsapp_templates._normalize_meta_status("APPROVED"), "APPROVED")
        self.assertEqual(whatsapp_templates._normalize_meta_status("PENDING"), "IN_REVIEW")
        self.assertEqual(whatsapp_templates._normalize_meta_status("FLAGGED"), "REJECTED")
        self.assertEqual(whatsapp_templates._normalize_meta_status(None), "DRAFT")
        # an unknown status falls back to IN_REVIEW (never trusts a raw value)
        self.assertEqual(whatsapp_templates._normalize_meta_status("SOMETHING"), "IN_REVIEW")
