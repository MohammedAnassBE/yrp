# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from yrp import whatsapp


class TestDeliverWhatsAppTemplate(IntegrationTestCase):
    def test_template_only_send_returns_ok_and_meta_message_id(self):
        """A hub success carrying a meta_message_id yields ok=True and echoes the id.
        Also asserts send-time normalization: a name with a space/capital is
        lowercased + underscored before it reaches send_template_message."""
        captured = {}

        def fake_send(account, to_number, template_name, language_code, components):
            captured["template_name"] = template_name
            captured["components"] = components
            return True, {"success": True, "meta_message_id": "wamid.OK123",
                          "status_code": 200, "response": {"messages": [{"id": "wamid.OK123"}]}}

        with patch.object(whatsapp.whatsapp_hub_client, "send_template_message",
                          side_effect=fake_send):
            result = whatsapp.deliver_whatsapp_template(
                account_name="acct-1", to_number="919944405056",
                template_name="Order Update", language_code="en",
                body_vars={"1": "Hello"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["meta_message_id"], "wamid.OK123")
        self.assertIsNone(result["error"])
        # "Order Update" -> lowercased + underscored before the hub call
        self.assertEqual(captured["template_name"], "order_update")
        # body component present, no media header component
        types = [c["type"] for c in captured["components"]]
        self.assertIn("body", types)
        self.assertNotIn("header", types)

    def test_hub_failure_returns_not_ok_without_raising(self):
        """A Meta failure — the hub client returns (False, full_hub_dict) — is
        captured, not raised; error/meta_error/http_status land in the result."""
        def fake_send(account, to_number, template_name, language_code, components):
            return False, {
                "success": False,
                "error": "Template name does not match any approved template",
                "meta_error": {"code": 132001, "title": "Template name does not exist"},
                "status_code": 400,
            }

        with patch.object(whatsapp.whatsapp_hub_client, "send_template_message",
                          side_effect=fake_send):
            result = whatsapp.deliver_whatsapp_template(
                account_name="acct-1", to_number="919944405056",
                template_name="order_update", language_code="en",
                body_vars={"1": "Hello"})

        self.assertFalse(result["ok"])
        self.assertIsNone(result["meta_message_id"])
        self.assertEqual(result["error"], "Template name does not match any approved template")
        self.assertEqual(result["http_status"], 400)
        self.assertEqual(result["meta_error"]["code"], 132001)

    def test_over_cap_header_returns_failed_result_without_raising(self):
        """A header whose raw bytes exceed the effective cap fails locally — the
        hub upload is never attempted and no exception escapes."""
        big = b"x" * (30 * 1024 * 1024)  # 30 MB > IMAGE cap (5 MB)

        send_called = {"n": 0}
        upload_called = {"n": 0}

        def fake_send(*a, **k):
            send_called["n"] += 1
            return True, {"success": True, "meta_message_id": "wamid.SHOULD_NOT_HAPPEN"}

        def fake_upload(*a, **k):
            upload_called["n"] += 1
            return {"success": True, "media_id": "media-should-not-happen"}

        with patch.object(whatsapp, "_resolve_header_bytes",
                          return_value=(big, "image/jpeg", "big.jpg")):
            with patch.object(whatsapp.whatsapp_hub_client, "upload_media",
                              side_effect=fake_upload):
                with patch.object(whatsapp.whatsapp_hub_client, "send_template_message",
                                  side_effect=fake_send):
                    result = whatsapp.deliver_whatsapp_template(
                        account_name="acct-1", to_number="919944405056",
                        template_name="invoice", language_code="en",
                        header_source={"header_format": "IMAGE",
                                       "file_url": "/private/files/big.jpg"})

        self.assertFalse(result["ok"])
        self.assertIsNone(result["media_id"])
        self.assertIn("exceeds", (result["error"] or "").lower())
        self.assertEqual(upload_called["n"], 0, "over-cap must not reach the hub upload")
        self.assertEqual(send_called["n"], 0, "over-cap must not reach the hub send")

    def test_body_vars_ordered_by_int_for_ten_plus_variables(self):
        """A 12-variable dict must serialize in 1..12 int order, not string order
        (string sort would place '10','11','12' before '2')."""
        captured = {}

        def fake_send(account, to_number, template_name, language_code, components):
            captured["components"] = components
            return True, {"success": True, "meta_message_id": "wamid.ORD"}

        body_vars = {str(i): f"val{i}" for i in range(1, 13)}  # "1".."12"

        with patch.object(whatsapp.whatsapp_hub_client, "send_template_message",
                          side_effect=fake_send):
            result = whatsapp.deliver_whatsapp_template(
                account_name="acct-1", to_number="919944405056",
                template_name="big_template", language_code="en",
                body_vars=body_vars)

        self.assertTrue(result["ok"])
        body_comp = next(c for c in captured["components"] if c["type"] == "body")
        values = [p["text"] for p in body_comp["parameters"]]
        self.assertEqual(values, [f"val{i}" for i in range(1, 13)])
