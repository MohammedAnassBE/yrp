# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase


class TestYRPWhatsAppWebhookLog(IntegrationTestCase):
    def test_payload_not_required_raw_body_captured(self):
        # The reference dropped garbage bodies (payload reqd:1). Ours must log a
        # body-less / malformed POST verbatim, so payload is NOT reqd and the raw
        # text is always stored — exactly the inputs you most want logged.
        log = frappe.get_doc({
            "doctype": "YRP WhatsApp Webhook Log",
            "raw": "not-json-garbage-body",
            "event_type": "other",
        })
        log.insert(ignore_permissions=True)
        self.assertTrue(log.name)
        self.assertEqual(log.processed, 0)
        self.assertEqual(log.raw, "not-json-garbage-body")

    def test_observability_counters(self):
        log = frappe.get_doc({
            "doctype": "YRP WhatsApp Webhook Log",
            "payload": '{"statuses": []}',
            "event_type": "status_update",
            "statuses_applied": 2,
            "inbound_messages": 1,
            "processed": 1,
        })
        log.insert(ignore_permissions=True)
        self.assertEqual(log.statuses_applied, 2)
        self.assertEqual(log.inbound_messages, 1)
        self.assertEqual(log.processed, 1)
