"""Tests for the MSG91 Flow (template-id) SMS path: config resolution, number
normalisation, JSON-body success/error handling (the incident case: HTTP 200
with type=error must NOT count as sent), and the send_flow_sms_notification API
+ SMS Notification Log writing."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_purchase_order,
	_supplier,
	_warehouse,
)
from yrp.yrp.doctype.supplier.test_supplier_notification import _contact_for

POST = "requests.post"


def _resp(status, payload=None, text=None):
	"""Fake requests.Response. `payload` (dict) is returned by .json();
	`text` is the raw body. If payload is None, .json() raises ValueError."""
	m = MagicMock()
	m.status_code = status
	m.text = text if text is not None else (frappe.as_json(payload) if payload is not None else "not-json")
	if payload is None:
		m.json.side_effect = ValueError("no json")
	else:
		m.json.return_value = payload
	return m


class TestYRPSMSSettings(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self._configure(rows=[{"template_name": "PO Reminder", "reference_doctype": "Purchase Order",
			"template_id": "tmpl-PO", "authkey": "secret-key", "template_body": "Hi {supplier}, order {name} ready"}])

	def _configure(self, rows, gateway_url="https://control.msg91.com/api/v5/flow/",
			country_code="91", enabled=1):
		doc = frappe.get_doc("YRP SMS Settings")
		doc.enabled = enabled
		doc.sms_gateway_url = gateway_url
		doc.country_code = country_code
		doc.templates = []
		for row in rows:
			doc.append("templates", row)
		doc.save(ignore_permissions=True)
		frappe.clear_cache(doctype="YRP SMS Settings")

	# ---- config resolution ---------------------------------------------

	def test_config_resolves_template_and_authkey(self):
		from yrp.yrp.doctype.yrp_sms_settings.yrp_sms_settings import get_sms_config
		cfg = get_sms_config("Purchase Order")
		self.assertEqual(cfg["template_id"], "tmpl-PO")
		self.assertEqual(cfg["template_name"], "PO Reminder")
		self.assertEqual(cfg["authkey"], "secret-key")
		self.assertEqual(cfg["country_code"], "91")

	def test_missing_config_raises(self):
		from yrp.yrp.doctype.yrp_sms_settings.yrp_sms_settings import get_sms_config
		with self.assertRaises(frappe.ValidationError):
			get_sms_config("Delivery Challan")

	def test_disabled_raises(self):
		self._configure(rows=[{"template_name": "PO Reminder", "reference_doctype": "Purchase Order",
			"template_id": "tmpl-PO", "authkey": "secret-key"}], enabled=0)
		from yrp.yrp.doctype.yrp_sms_settings.yrp_sms_settings import get_sms_config
		with self.assertRaises(frappe.ValidationError):
			get_sms_config("Purchase Order")

	def test_multiple_templates_disambiguated_by_name(self):
		self._configure(rows=[
			{"template_name": "Reminder", "reference_doctype": "Purchase Order",
				"template_id": "tmpl-A", "authkey": "key-A"},
			{"template_name": "Cancel Notice", "reference_doctype": "Purchase Order",
				"template_id": "tmpl-B", "authkey": "key-B"},
		])
		from yrp.yrp.doctype.yrp_sms_settings.yrp_sms_settings import get_sms_config
		self.assertEqual(get_sms_config("Purchase Order", "Cancel Notice")["template_id"], "tmpl-B")
		self.assertEqual(get_sms_config("Purchase Order", "Reminder")["template_id"], "tmpl-A")

	def test_parse_template_variables(self):
		from yrp.yrp.doctype.yrp_sms_settings.yrp_sms_settings import parse_template_variables
		self.assertEqual(
			parse_template_variables("Hi {supplier}, order {name} for {supplier} at {DocType}"),
			["supplier", "name", "DocType"],
		)

	# ---- number normalisation ------------------------------------------

	def test_normalise_prefixes_country_code_on_10_digits(self):
		from yrp.sms import _normalise_number
		self.assertEqual(_normalise_number("9944405056", "91"), "919944405056")

	def test_normalise_leaves_prefixed_number(self):
		from yrp.sms import _normalise_number
		self.assertEqual(_normalise_number("919944405056", "91"), "919944405056")

	def test_normalise_strips_formatting(self):
		from yrp.sms import _normalise_number
		self.assertEqual(_normalise_number("+91 99444-05056", "91"), "919944405056")

	# ---- deliver_flow_sms ----------------------------------------------

	def test_deliver_success(self):
		from yrp.sms import deliver_flow_sms
		with patch(POST, return_value=_resp(200, {"type": "success", "message": "req-123"})) as p:
			result = deliver_flow_sms(reference_doctype="Purchase Order", mobile_no="9944405056",
				params={"VAR1": "Acme"})
		self.assertTrue(result["ok"])
		self.assertEqual(result["request_id"], "req-123")
		self.assertEqual(result["response_type"], "success")
		self.assertIsNone(result["error"])
		# request shape (MSG91 Flow): authkey header + JSON body {template_id, recipients:[{mobiles, **vars}]}
		body = p.call_args.kwargs["json"]
		self.assertEqual(body["template_id"], "tmpl-PO")
		recipient = body["recipients"][0]
		self.assertEqual(recipient["mobiles"], "919944405056")
		self.assertEqual(recipient["VAR1"], "Acme")
		self.assertEqual(p.call_args.kwargs["headers"]["authkey"], "secret-key")

	def test_deliver_success_reads_message_field(self):
		"""Flow returns the request id in `message`; a bare `request_id` still works as fallback."""
		from yrp.sms import deliver_flow_sms
		with patch(POST, return_value=_resp(200, {"type": "success", "request_id": "flow-req-77"})):
			result = deliver_flow_sms(reference_doctype="Purchase Order", mobile_no="9944405056")
		self.assertTrue(result["ok"])
		self.assertEqual(result["request_id"], "flow-req-77")

	def test_deliver_http200_but_type_error_is_not_sent(self):
		"""The incident: MSG91 returns HTTP 200 but rejects the message."""
		from yrp.sms import deliver_flow_sms
		with patch(POST, return_value=_resp(200, {"type": "error", "message": "template not found"})):
			result = deliver_flow_sms(reference_doctype="Purchase Order", mobile_no="9944405056")
		self.assertFalse(result["ok"])
		self.assertIsNone(result["request_id"])
		self.assertEqual(result["response_type"], "error")
		self.assertIn("template not found", result["error"])

	def test_deliver_no_config_captured_as_failure(self):
		from yrp.sms import deliver_flow_sms
		with patch(POST) as p:
			result = deliver_flow_sms(reference_doctype="Stock Entry", mobile_no="9944405056")
		self.assertFalse(result["ok"])
		self.assertIn("No SMS template configured", result["error"])
		p.assert_not_called()

	def test_deliver_non_json_response_is_failure(self):
		from yrp.sms import deliver_flow_sms
		with patch(POST, return_value=_resp(502, payload=None, text="<html>Bad Gateway</html>")):
			result = deliver_flow_sms(reference_doctype="Purchase Order", mobile_no="9944405056")
		self.assertFalse(result["ok"])
		self.assertIsNone(result["request_id"])
		self.assertIn("Bad Gateway", result["error"])

	# ---- send_flow_sms_notification + log ------------------------------

	def _po_with_contact(self):
		supplier = _supplier(f"_T Flow SMS Supplier {frappe.generate_hash(length=4)}")
		_contact_for(supplier, mobile="9944405056")
		return _purchase_order(qty=1, warehouse=_warehouse("_T Flow SMS WH"), supplier=supplier)

	def test_send_writes_sent_log(self):
		po = self._po_with_contact()
		from yrp.notification import send_flow_sms_notification
		with patch(POST, return_value=_resp(200, {"type": "success", "message": "req-999"})):
			send_flow_sms_notification("Purchase Order", po.name, template_name="PO Reminder",
				mobile_no="9944405056", params={"supplier": "X"})
		log = frappe.get_last_doc("SMS Notification Log",
			filters={"reference_doctype": "Purchase Order", "reference_name": po.name})
		self.assertEqual(log.status, "Sent")
		self.assertEqual(log.send_path, "Flow")
		self.assertEqual(log.template_id, "tmpl-PO")
		self.assertEqual(log.template_name, "PO Reminder")
		self.assertEqual(log.request_id, "req-999")
		self.assertEqual(log.mobile_no, "9944405056")

	def test_send_failure_writes_failed_log_and_does_not_throw(self):
		po = self._po_with_contact()
		from yrp.notification import send_flow_sms_notification
		with patch(POST, return_value=_resp(200, {"type": "error", "message": "DND"})):
			send_flow_sms_notification("Purchase Order", po.name, template_name="PO Reminder",
				mobile_no="9944405056")
		log = frappe.get_last_doc("SMS Notification Log",
			filters={"reference_doctype": "Purchase Order", "reference_name": po.name})
		self.assertEqual(log.status, "Failed")
		self.assertEqual(log.send_path, "Flow")
		self.assertIn("DND", log.error)

	def test_resend_flow_log_uses_flow_path(self):
		"""A Failed flow log resends via the Flow API (params from log.message),
		flips to Sent, and never falls through to the legacy free-text path."""
		po = self._po_with_contact()
		from yrp.notification import send_flow_sms_notification, resend_sms_notification_log
		with patch(POST, return_value=_resp(200, {"type": "error", "message": "temporary"})):
			send_flow_sms_notification("Purchase Order", po.name, template_name="PO Reminder",
				mobile_no="9944405056", params={"supplier": "Acme"})
		log = frappe.get_last_doc("SMS Notification Log",
			filters={"reference_doctype": "Purchase Order", "reference_name": po.name})
		self.assertEqual(log.status, "Failed")
		with patch(POST, return_value=_resp(200, {"type": "success", "message": "req-resend"})) as p:
			resend_sms_notification_log(log.name)
		log.reload()
		self.assertEqual(log.status, "Sent")
		self.assertEqual(log.request_id, "req-resend")
		# resent down the Flow path with the stored template + params in the JSON body
		body = p.call_args.kwargs["json"]
		self.assertEqual(body["template_id"], "tmpl-PO")
		self.assertEqual(body["recipients"][0]["supplier"], "Acme")
