# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

from frappe.tests import IntegrationTestCase

from yrp.yrp.doctype.whatsapp_notification_log.whatsapp_notification_log import (
    create_whatsapp_log,
)


class TestWhatsAppNotificationLog(IntegrationTestCase):
    def test_create_log_sent_on_ok(self):
        log = create_whatsapp_log(
            reference_doctype="Supplier",
            reference_name="_T WA Log Ref",
            supplier=None,
            contact=None,
            mobile_no="919876543210",
            result={
                "ok": True,
                "meta_message_id": "wamid.TEST123",
                "http_status": 200,
                "raw": '{"success": true}',
                "error": None,
                "meta_error": None,
                "media_id": None,
                "media_mime": None,
                "file_name": None,
            },
            template_name="order_ready",
            language_code="en",
            message="Your order is ready",
        )
        self.assertEqual(log.status, "Sent")
        self.assertEqual(log.meta_message_id, "wamid.TEST123")
        self.assertEqual(log.http_status, 200)
        self.assertTrue(log.sent_at)

    def test_create_log_failed_on_not_ok_and_sent_at_null(self):
        log = create_whatsapp_log(
            reference_doctype="Supplier",
            reference_name="_T WA Log Ref",
            supplier=None,
            contact=None,
            mobile_no="919876543210",
            result={
                "ok": False,
                "meta_message_id": None,
                "http_status": 400,
                "raw": '{"success": false}',
                "error": "bad number",
                "meta_error": '{"code": 131000}',
                "media_id": None,
                "media_mime": None,
                "file_name": None,
            },
        )
        self.assertEqual(log.status, "Failed")
        self.assertIsNone(log.sent_at)
        self.assertEqual(log.error, "bad number")
        self.assertEqual(log.http_status, 400)

    def test_create_log_survives_deleted_reference(self):
        # reference_name / supplier point at a Supplier that does not exist;
        # ignore_links must let this append-only audit row insert anyway
        # (create_sms_log contract). The JSON blobs must round-trip as strings
        # so a resend can rebuild header_source / message_variables.
        log = create_whatsapp_log(
            reference_doctype="Supplier",
            reference_name="_T WA Nonexistent Supplier",
            supplier="_T WA Nonexistent Supplier",
            contact=None,
            mobile_no="919876543210",
            result={
                "ok": True,
                "meta_message_id": "wamid.X",
                "http_status": 200,
                "raw": "{}",
            },
            message_variables={"header_vars": [], "body_vars": ["A", "B"]},
            header_source={"header_format": "DOCUMENT", "print_format": "Standard"},
        )
        self.assertTrue(log.name)
        self.assertEqual(log.status, "Sent")
        self.assertIn("body_vars", log.message_variables)
        self.assertIn("DOCUMENT", log.header_source)
