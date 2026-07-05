# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import json
from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from yrp import whatsapp_hub_client as hub_client


class _FakeSettings:
    """Stand-in for the YRP WhatsApp Hub Settings Single — only the two
    resolvers _call_hub_api touches. get_hub_url() returns an already
    trailing-slash-stripped URL, exactly like the real resolver."""

    def get_hub_url(self):
        return "https://hub.example.com"

    def get_hub_auth_headers(self):
        return {"Authorization": "token KEY:SECRET", "Content-Type": "application/json"}


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestWhatsAppHubClient(IntegrationTestCase):
    def test_call_hub_api_builds_url_token_header_and_unwraps_message(self):
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["timeout"] = timeout
            return _FakeResponse({"message": {"success": True, "meta_message_id": "wamid.X"}})

        with patch.object(hub_client, "_get_settings", return_value=_FakeSettings()):
            with patch.object(hub_client.requests, "post", side_effect=fake_post):
                result = hub_client._call_hub_api("send.send_template", {"to_number": "9199"})

        self.assertEqual(
            captured["url"],
            "https://hub.example.com/api/method/"
            "frappe_whatsapp_integration.frappe_whatsapp_hub.api.send.send_template",
        )
        self.assertEqual(captured["headers"]["Authorization"], "token KEY:SECRET")
        self.assertEqual(captured["json"], {"to_number": "9199"})
        self.assertEqual(captured["timeout"], 30)
        # Frappe's {message: …} envelope is unwrapped:
        self.assertEqual(result, {"success": True, "meta_message_id": "wamid.X"})

    def test_send_template_routes_document_header_with_id_and_lowercase_format(self):
        captured = {}

        def fake_call_hub_api(method, data=None):
            captured["method"] = method
            captured["data"] = data
            return {"success": True, "meta_message_id": "wamid.DOC"}

        with patch.object(hub_client, "_call_hub_api", side_effect=fake_call_hub_api):
            with patch.object(hub_client, "_get_account_name", return_value="acct-1"):
                components = [
                    {"type": "header", "parameters": [
                        {"type": "document",
                         "document": {"id": "MEDIA123", "link": "https://cdn/po.pdf",
                                      "filename": "po.pdf"}}
                    ]},
                    {"type": "body", "parameters": [
                        {"type": "text", "text": "PO-001"},
                        {"type": "text", "text": "5000"},
                    ]},
                ]
                ok, resp = hub_client.send_template_message(
                    "acct-1", "919900000000", "purchase_order", "en", components
                )

        self.assertTrue(ok)
        self.assertEqual(captured["method"], "send.send_template_with_document")
        self.assertEqual(captured["data"]["header_format"], "document")
        self.assertEqual(captured["data"]["document_id"], "MEDIA123")
        self.assertEqual(captured["data"]["document_url"], "https://cdn/po.pdf")
        self.assertEqual(captured["data"]["document_filename"], "po.pdf")
        self.assertEqual(captured["data"]["account_name"], "acct-1")
        self.assertEqual(json.loads(captured["data"]["body_variables"]), ["PO-001", "5000"])

    def test_send_template_text_only_uses_regular_endpoint(self):
        captured = {}

        def fake_call_hub_api(method, data=None):
            captured["method"] = method
            captured["data"] = data
            return {"success": True, "meta_message_id": "wamid.TXT"}

        with patch.object(hub_client, "_call_hub_api", side_effect=fake_call_hub_api):
            with patch.object(hub_client, "_get_account_name", return_value="acct-1"):
                components = [
                    {"type": "body", "parameters": [{"type": "text", "text": "Hi"}]},
                ]
                ok, resp = hub_client.send_template_message(
                    "acct-1", "919900000000", "hello", "en", components
                )

        self.assertTrue(ok)
        self.assertEqual(captured["method"], "send.send_template")
        self.assertEqual(json.loads(captured["data"]["body_variables"]), ["Hi"])

    def test_send_template_hub_failure_returns_false_and_full_dict(self):
        # On a Meta failure the client returns (False, full_hub_dict) so the
        # caller can log meta_error + http_status, not just the error string.
        def fake_call_hub_api(method, data=None):
            return {"success": False, "error": "template not approved",
                    "meta_error": {"code": 132001}, "status_code": 400}

        with patch.object(hub_client, "_call_hub_api", side_effect=fake_call_hub_api):
            with patch.object(hub_client, "_get_account_name", return_value="acct-1"):
                components = [{"type": "body", "parameters": [{"type": "text", "text": "Hi"}]}]
                ok, resp = hub_client.send_template_message(
                    "acct-1", "919900000000", "hello", "en", components
                )

        self.assertFalse(ok)
        self.assertIsInstance(resp, dict)
        self.assertEqual(resp["error"], "template not approved")
        self.assertEqual(resp["meta_error"]["code"], 132001)
        self.assertEqual(resp["status_code"], 400)

    def test_interactive_and_text_wrappers_dropped(self):
        # Regression guard — the scoped copy must NOT carry the interactive /
        # text / personal / download wrappers from the reference hub_client.
        self.assertFalse(hasattr(hub_client, "send_text_message"),
                         "send_text_message must be dropped in the scoped spoke client")
        self.assertFalse(hasattr(hub_client, "send_interactive_message"),
                         "send_interactive_message must be dropped in the scoped spoke client")
        self.assertFalse(hasattr(hub_client, "download_media_from_meta"),
                         "download_media_from_meta must be dropped in the scoped spoke client")
