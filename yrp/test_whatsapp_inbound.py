# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from yrp.whatsapp_inbound import process_payload, process_status_update
from yrp.yrp.doctype.whatsapp_notification_log.whatsapp_notification_log import (
    create_whatsapp_log,
)


class TestWhatsAppInbound(IntegrationTestCase):
    def _make_sent_log(self, meta_message_id):
        """Seed a WhatsApp Notification Log at status Sent with a known
        meta_message_id (the shape create_whatsapp_log writes after a
        successful send)."""
        return create_whatsapp_log(
            reference_doctype="Supplier",
            reference_name="_T WA Inbound Ref",
            supplier=None,
            contact=None,
            mobile_no="919876500000",
            result={
                "ok": True,
                "meta_message_id": meta_message_id,
                "http_status": 200,
                "raw": "{}",
            },
            template_name="order_ready",
            language_code="en",
            message="hi",
        )

    def _make_template(self, template_name):
        """Seed a DRAFT YRP WhatsApp Template (from_meta_sync so validate()
        keeps the given status instead of forcing DRAFT for a manual doc)."""
        fq = f"{template_name}-en"
        if frappe.db.exists("YRP WhatsApp Template", fq):
            frappe.delete_doc(
                "YRP WhatsApp Template", fq, ignore_permissions=True, force=True
            )
        acct = "yrp-wa-inbound-acct"
        if not frappe.db.exists("YRP WhatsApp Account", acct):
            frappe.get_doc(
                {
                    "doctype": "YRP WhatsApp Account",
                    "account_name": acct,
                    "enabled": 1,
                }
            ).insert(ignore_permissions=True)
        doc = frappe.get_doc(
            {
                "doctype": "YRP WhatsApp Template",
                "template_name": template_name,
                "language_code": "en",
                "category": "UTILITY",
                "status": "DRAFT",
                "whatsapp_account": acct,
                "body_text": "Order {{1}} ready.",
            }
        )
        doc.flags.from_meta_sync = True
        doc.insert(ignore_permissions=True)
        return doc

    def test_sent_delivered_read_advance(self):
        log = self._make_sent_log("wamid.MONO1")
        self.assertEqual(log.status, "Sent")

        self.assertTrue(
            process_status_update({"id": "wamid.MONO1", "status": "delivered"})
        )
        log.reload()
        self.assertEqual(log.status, "Delivered")
        self.assertTrue(log.delivered_at)

        self.assertTrue(
            process_status_update({"id": "wamid.MONO1", "status": "read"})
        )
        log.reload()
        self.assertEqual(log.status, "Read")
        self.assertTrue(log.read_at)

    def test_out_of_order_read_then_delivered_stays_read(self):
        log = self._make_sent_log("wamid.OOO")
        self.assertTrue(
            process_status_update({"id": "wamid.OOO", "status": "read"})
        )
        # a delivered arriving AFTER read is a lower rank -> must not regress
        self.assertFalse(
            process_status_update({"id": "wamid.OOO", "status": "delivered"})
        )
        log.reload()
        self.assertEqual(log.status, "Read")
        self.assertIsNone(log.delivered_at)

    def test_failed_after_read_ignored(self):
        log = self._make_sent_log("wamid.FAR")
        self.assertTrue(
            process_status_update({"id": "wamid.FAR", "status": "read"})
        )
        # failed is off-axis: it must never clobber a positive terminal (Read)
        self.assertFalse(
            process_status_update(
                {
                    "id": "wamid.FAR",
                    "status": "failed",
                    "errors": [{"code": 131000, "title": "boom"}],
                }
            )
        )
        log.reload()
        self.assertEqual(log.status, "Read")
        self.assertFalse(log.error)

    def test_unknown_meta_message_id_ignored(self):
        self.assertFalse(
            process_status_update({"id": "wamid.NOPE", "status": "delivered"})
        )

    def test_template_status_update_sets_approved(self):
        tpl = self._make_template("yrp_wa_inbound_tpl")
        self.assertEqual(tpl.status, "DRAFT")
        process_payload(
            {
                "template_status_update": {
                    "message_template_name": "yrp_wa_inbound_tpl",
                    "event": "APPROVED",
                }
            }
        )
        tpl.reload()
        self.assertEqual(tpl.status, "APPROVED")

    def test_process_payload_routes_statuses_and_counts_messages(self):
        log = self._make_sent_log("wamid.PP1")
        webhook_log = frappe.get_doc(
            {"doctype": "YRP WhatsApp Webhook Log", "raw": "{}"}
        )
        webhook_log.insert(ignore_permissions=True)
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "statuses": [
                                    {"id": "wamid.PP1", "status": "delivered"}
                                ],
                                "messages": [
                                    {"from": "919876500000", "type": "text"}
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        process_payload(payload, webhook_log=webhook_log)

        log.reload()
        self.assertEqual(log.status, "Delivered")
        # messages[] are COUNTED ONLY (the future-inbox seam), not persisted
        self.assertEqual(webhook_log.statuses_applied, 1)
        self.assertEqual(webhook_log.inbound_messages, 1)
        self.assertEqual(webhook_log.event_type, "status_update")
        self.assertEqual(webhook_log.processed, 1)
