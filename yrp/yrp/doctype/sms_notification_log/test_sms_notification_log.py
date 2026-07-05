from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

SEND_REQUEST = "yrp.yrp.doctype.notification_template.notification_template.send_request"


def _resp(status, text):
	m = MagicMock()
	m.status_code = status
	m.text = text
	return m


class TestDeliverSms(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.set_single_value("SMS Settings", "sms_gateway_url", "http://127.0.0.1:8899/send")
		frappe.db.set_single_value("SMS Settings", "message_parameter", "msg")
		frappe.db.set_single_value("SMS Settings", "receiver_parameter", "to")

	def test_deliver_sms_captures_request_id_on_success(self):
		from yrp.sms import deliver_sms

		with patch(SEND_REQUEST, return_value=_resp(200, "6gdpirgbH1CO")) as mock_send:
			result = deliver_sms("hello", "98765 43210")
		self.assertTrue(result["ok"])
		self.assertEqual(result["http_status"], 200)
		self.assertEqual(result["request_id"], "6gdpirgbH1CO")
		self.assertEqual(mock_send.call_args.args[1]["to"], "9876543210")
		self.assertEqual(mock_send.call_args.args[1]["msg"], "hello")

	def test_deliver_sms_marks_failure_on_error_body(self):
		from yrp.sms import deliver_sms

		with patch(SEND_REQUEST, return_value=_resp(400, '{"message":"bad number"}')):
			result = deliver_sms("hello", "98765 43210")
		self.assertFalse(result["ok"])
		self.assertIn("bad number", result["error"])

	def test_deliver_sms_blank_number_returns_failure_not_raises(self):
		from yrp.sms import deliver_sms

		result = deliver_sms("hello", "")
		self.assertFalse(result["ok"])
		self.assertTrue(result["error"])

	def test_create_sms_log_row(self):
		from yrp.yrp.doctype.sms_notification_log.sms_notification_log import create_sms_log

		log = create_sms_log(
			reference_doctype="Supplier", reference_name="_T SMS Log Ref",
			supplier=None, contact=None, mobile_no="9876543210", template=None,
			message="hi", result={"ok": True, "http_status": 200, "request_id": "ABC", "raw": "ABC"},
		)
		self.assertEqual(log.status, "Sent")
		self.assertEqual(log.request_id, "ABC")
		self.assertTrue(log.sent_at)
