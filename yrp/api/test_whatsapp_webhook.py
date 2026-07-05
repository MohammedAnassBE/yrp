# Copyright (c) 2026, Essdee and contributors
# For license information, please see license.txt

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import set_request

from yrp.api.whatsapp_webhook import receive, receive_push

_WEBHOOK_USER = "yrp-wa-webhook-test@essdee.local"


def _unique_status_payload():
	"""A well-formed, per-run-unique delivery-status payload.

	The ``meta_message_id`` is random so ``process_payload`` finds no matching
	``WhatsApp Notification Log`` and treats it as a harmless no-op, and so the
	fail-open replay guard never collides with a prior run's Redis key.
	"""
	mid = "wamid.test_" + frappe.generate_hash(length=12)
	return {
		"entry": [
			{
				"changes": [
					{
						"value": {
							"metadata": {"phone_number_id": "test_pnid"},
							"statuses": [{"id": mid, "status": "delivered"}],
						}
					}
				]
			}
		]
	}


def _post(body, content_type="application/json"):
	"""Simulate an inbound POST so the endpoint can read the raw request body."""
	data = body if isinstance(body, (bytes, str)) else json.dumps(body)
	set_request(
		method="POST",
		path="/api/method/yrp.api.whatsapp_webhook.receive",
		data=data,
		content_type=content_type,
	)


class TestWhatsAppWebhookReceive(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("User", _WEBHOOK_USER):
			frappe.get_doc({
				"doctype": "User",
				"email": _WEBHOOK_USER,
				"first_name": "YRP WA Webhook",
				"send_welcome_email": 0,
				"enabled": 1,
				"user_type": "System User",
			}).insert(ignore_permissions=True)
		frappe.db.set_single_value(
			"YRP WhatsApp Hub Settings", "webhook_user", _WEBHOOK_USER
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None

	# --- the webhook_user pin ------------------------------------------------

	def test_guest_is_rejected(self):
		frappe.set_user("Guest")
		_post(_unique_status_payload())
		with self.assertRaises(frappe.PermissionError):
			receive()

	def test_wrong_authenticated_user_is_rejected(self):
		frappe.set_user("Administrator")  # authenticated, but != webhook_user
		_post(_unique_status_payload())
		with self.assertRaises(frappe.PermissionError):
			receive()

	# --- happy path ----------------------------------------------------------

	def test_webhook_user_accepted_returns_ok_and_writes_log(self):
		frappe.set_user(_WEBHOOK_USER)
		_post(_unique_status_payload())
		before = frappe.db.count("YRP WhatsApp Webhook Log")
		result = receive()
		self.assertEqual(result, {"ok": True})
		after = frappe.db.count("YRP WhatsApp Webhook Log")
		self.assertEqual(after, before + 1, "a webhook-log row must be written")

	# --- resilience ----------------------------------------------------------

	def test_processing_exception_still_returns_ok(self):
		frappe.set_user(_WEBHOOK_USER)
		_post(_unique_status_payload())
		with patch(
			"yrp.whatsapp_inbound.process_payload",
			side_effect=Exception("boom"),
		):
			result = receive()
		self.assertEqual(result, {"ok": True})

	def test_malformed_body_is_still_logged(self):
		frappe.set_user(_WEBHOOK_USER)
		garbage = '{"entry": [ this is not valid json'
		_post(garbage)
		before = frappe.db.count("YRP WhatsApp Webhook Log")
		result = receive()
		self.assertEqual(result, {"ok": True})
		after = frappe.db.count("YRP WhatsApp Webhook Log")
		self.assertEqual(after, before + 1)
		latest = frappe.get_all(
			"YRP WhatsApp Webhook Log",
			fields=["payload", "raw"],
			order_by="creation desc",
			limit=1,
		)
		# A malformed body does not parse, so `payload` stays null but the
		# verbatim bytes are preserved in `raw`.
		self.assertEqual(latest[0].raw, garbage)
		self.assertIsNone(latest[0].payload)


class TestWhatsAppWebhookReceivePush(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("User", _WEBHOOK_USER):
			frappe.get_doc({
				"doctype": "User",
				"email": _WEBHOOK_USER,
				"first_name": "YRP WA Webhook",
				"send_welcome_email": 0,
				"enabled": 1,
				"user_type": "System User",
			}).insert(ignore_permissions=True)
		frappe.db.set_single_value(
			"YRP WhatsApp Hub Settings", "webhook_user", _WEBHOOK_USER
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None

	def test_receive_push_pin_rejects_wrong_user(self):
		frappe.set_user("Administrator")
		_post({"template": {"id": "1", "name": "x"}})
		with self.assertRaises(frappe.PermissionError):
			receive_push()

	def test_receive_push_upserts_and_returns_name(self):
		frappe.set_user(_WEBHOOK_USER)
		_post({"template": {"id": "999", "name": "welcome"}})
		with patch(
			"yrp.whatsapp_templates._upsert_local_template",
			return_value="welcome-en",
		) as up, patch(
			"yrp.whatsapp_hub_client._get_account_name",
			return_value="Test Account",
		):
			result = receive_push()
		self.assertEqual(result, {"upserted": 1, "name": "welcome-en"})
		up.assert_called_once()
