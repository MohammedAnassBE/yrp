"""Tests for supplier notification: contact resolution, channel error
surfacing (this file, Task 1) and the yrp.notification API (Task 2)."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.goods_received_note.test_purchase_order_grn import (
	_purchase_order,
	_supplier,
	_warehouse,
)

# Patch target: the module-level gateway call inside the Notification
# Template controller (the fork that shadows frappe core's send_sms).
SEND_REQUEST = "yrp.yrp.doctype.notification_template.notification_template.send_request"


def _gw(status, text="OK"):
	from unittest.mock import MagicMock
	m = MagicMock(); m.status_code = status; m.text = text
	return m


class _NotificationTestBase(FrappeTestCase):
	"""Shared setUp. v16's compat FrappeTestCase rolls back per CLASS, not
	per test — so each test must clear templates itself, inside the class
	transaction (also shields against templates already committed on the
	test site)."""

	def setUp(self):
		super().setUp()
		frappe.db.delete("Notification Template")
		# No outgoing Email Account on the test site; muting makes the email
		# leg use the dummy account instead of raising OutgoingEmailError
		# (which subclasses Exception, NOT ValidationError, in v16).
		frappe.flags.mute_emails = True
		self.addCleanup(setattr, frappe.flags, "mute_emails", False)
		frappe.db.set_single_value("SMS Settings", "sms_gateway_url", "http://127.0.0.1:8899/send")
		frappe.db.set_single_value("SMS Settings", "message_parameter", "msg")
		frappe.db.set_single_value("SMS Settings", "receiver_parameter", "to")


def _contact_for(supplier, mobile=None, email=None):
	contact = frappe.get_doc({
		"doctype": "Contact",
		"first_name": f"_T SMS Contact {frappe.generate_hash(length=6)}",
		"is_primary_contact": 1,
		"links": [{"link_doctype": "Supplier", "link_name": supplier}],
	})
	if mobile:
		contact.append("phone_nos", {"phone": mobile, "is_primary_mobile_no": 1})
	if email:
		contact.append("email_ids", {"email_id": email, "is_primary": 1})
	contact.insert(ignore_permissions=True)
	return contact


def _contact_with_phones(supplier, phones):
	"""phones: list of (number, is_primary_mobile) tuples.

	Rows are written via Contact Phone's db_insert() (a raw DB write with no
	controller/field validation) rather than Contact.append(...).insert().
	Reason: Contact Phone's own `phone` field has options="Phone", so ANY row
	-- primary or not -- is checked by frappe.utils.validate_phone_number
	(PHONE_NUMBER_PATTERN caps at 20 chars) the instant the parent Contact is
	saved through the normal Document API; a comma-joined test value like
	"9000000002,9000000003" (21 chars) -- exactly the "one field holds two
	numbers" case this fixture exists to cover -- fails that check before our
	own code ever runs. db_insert() sidesteps that for just these rows; every
	consumer we care about (get_primary_contact_details, _extract_numbers)
	re-reads the row via frappe.get_doc/get_all, so what they see is
	unaffected by how the row was written.
	"""
	contact = frappe.get_doc({
		"doctype": "Contact",
		"first_name": f"_T SMS Multi {frappe.generate_hash(length=6)}",
		"is_primary_contact": 1,
		"links": [{"link_doctype": "Supplier", "link_name": supplier}],
	})
	contact.insert(ignore_permissions=True)
	for idx, (number, primary) in enumerate(phones, start=1):
		row = frappe.get_doc({
			"doctype": "Contact Phone",
			"parent": contact.name,
			"parenttype": "Contact",
			"parentfield": "phone_nos",
			"idx": idx,
			"docstatus": 0,
			"phone": number,
			"is_primary_mobile_no": 1 if primary else 0,
		})
		row.db_insert()
	return contact


def _notification_template(name, channel="SMS", document_type="Purchase Order",
		event="Submit", body="PO {{ doc.name }} for {{ doc.supplier }}"):
	if frappe.db.exists("Notification Template", name):
		return frappe.get_doc("Notification Template", name)
	template = frappe.get_doc({
		"doctype": "Notification Template",
		"__newname": name,
		"enabled": 1,
		"channel": channel,
		"document_type": document_type,
		"event": event,
		"template": body,
	})
	template.insert(ignore_permissions=True)
	return template


class TestSupplierNotification(_NotificationTestBase):
	def _supplier_with_contact(self, mobile="98765 43210", email=None):
		supplier = _supplier(f"_T SMS Supplier {frappe.generate_hash(length=6)}")
		_contact_for(supplier, mobile=mobile, email=email)
		return supplier

	def test_get_primary_contact_details(self):
		supplier = self._supplier_with_contact(mobile="98765 43210", email="s@example.com")
		details = frappe.get_doc("Supplier", supplier).get_primary_contact_details()
		self.assertEqual(details["mobile"], "98765 43210")
		self.assertEqual(details["email"], "s@example.com")
		self.assertTrue(details["contact"])

	def test_no_default_contact_throws(self):
		supplier = _supplier(f"_T SMS NoContact {frappe.generate_hash(length=6)}")
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc("Supplier", supplier).get_primary_contact_details()

	def test_sms_without_mobile_throws(self):
		supplier = self._supplier_with_contact(mobile=None, email="s@example.com")
		_notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=1, warehouse=_warehouse("_T SMS WH"), supplier=supplier)
		with patch(SEND_REQUEST, return_value=_gw(200)) as mock_send:
			with self.assertRaises(frappe.ValidationError):
				frappe.get_doc("Supplier", supplier).send_notification(
					"Purchase Order", po.name, ["SMS"], "Submit")
		mock_send.assert_not_called()

	def test_partial_channels_skip_not_throw(self):
		# Email deliverable, SMS not: send email, warn about SMS, no throw.
		supplier = self._supplier_with_contact(mobile=None, email="s@example.com")
		suffix = frappe.generate_hash(length=6)
		_notification_template(f"_T PO Submit SMS {suffix}", channel="SMS")
		_notification_template(f"_T PO Submit Email {suffix}", channel="Email",
			body="Hello {{ doc.name }}")
		po = _purchase_order(qty=1, warehouse=_warehouse("_T SMS WH"), supplier=supplier)
		with patch(SEND_REQUEST, return_value=_gw(200)) as mock_send:
			frappe.get_doc("Supplier", supplier).send_notification(
				"Purchase Order", po.name, ["Email", "SMS"], "Submit")
		mock_send.assert_not_called()
		self.assertTrue(frappe.db.exists("Communication", {
			"reference_doctype": "Purchase Order",
			"reference_name": po.name,
			"communication_medium": "Email",
		}))

	def test_no_matching_template_sends_nothing(self):
		supplier = self._supplier_with_contact()
		po = _purchase_order(qty=1, warehouse=_warehouse("_T SMS WH"), supplier=supplier)
		# No template created for (Purchase Order, Submit, SMS) in this test's
		# transaction -> msgprint path, no gateway call, no exception.
		with patch(SEND_REQUEST, return_value=_gw(200)) as mock_send:
			frappe.get_doc("Supplier", supplier).send_notification(
				"Purchase Order", po.name, ["SMS"], "Submit")
		mock_send.assert_not_called()

	def test_send_sms_happy_path(self):
		supplier = self._supplier_with_contact(mobile="98765 43210")
		_notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		po = _purchase_order(qty=1, warehouse=_warehouse("_T SMS WH"), supplier=supplier)
		with patch(SEND_REQUEST, return_value=_gw(200)) as mock_send:
			frappe.get_doc("Supplier", supplier).send_notification(
				"Purchase Order", po.name, ["SMS"], "Submit")
		mock_send.assert_called_once()
		gateway_url, params = mock_send.call_args.args[0], mock_send.call_args.args[1]
		self.assertEqual(gateway_url, "http://127.0.0.1:8899/send")
		self.assertEqual(params["to"], "9876543210")  # validate_receiver_nos strips the space
		self.assertEqual(params["msg"], f"PO {po.name} for {supplier}")
		self.assertTrue(frappe.db.exists("Communication", {
			"reference_doctype": "Purchase Order",
			"reference_name": po.name,
			"communication_medium": "SMS",
		}))


class TestNotificationAPI(_NotificationTestBase):
	"""yrp.notification whitelisted wrappers (event derivation, supplier
	resolution, preview)."""

	def _submitted_po(self, mobile="98765 43210"):
		supplier = _supplier(f"_T SMS API Supplier {frappe.generate_hash(length=6)}")
		_contact_for(supplier, mobile=mobile)
		return _purchase_order(qty=1, warehouse=_warehouse("_T SMS WH"), supplier=supplier)

	def test_send_notification_derives_submit_event(self):
		from yrp.notification import send_notification

		po = self._submitted_po()  # docstatus 1 -> event Submit
		_notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		with patch(SEND_REQUEST, return_value=_gw(200)) as mock_send:
			send_notification("Purchase Order", po.name, channels=["SMS"])
		mock_send.assert_called_once()
		self.assertEqual(mock_send.call_args.args[1]["msg"], f"PO {po.name} for {po.supplier}")

	def test_send_notification_accepts_json_channel_string(self):
		from yrp.notification import send_notification

		po = self._submitted_po()
		_notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		with patch(SEND_REQUEST, return_value=_gw(200)) as mock_send:
			send_notification("Purchase Order", po.name, channels='["SMS"]')
		mock_send.assert_called_once()

	def test_doc_without_supplier_throws(self):
		from yrp.notification import send_notification

		warehouse = _warehouse(f"_T SMS NoSup WH {frappe.generate_hash(length=4)}")
		with self.assertRaises(frappe.ValidationError):
			send_notification("Warehouse", warehouse, channels=["SMS"])

	def test_sms_context_lists_all_enabled_templates_regardless_of_event(self):
		from yrp.notification import get_sms_context

		po = self._submitted_po()
		t1 = _notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}", event="Submit")
		t2 = _notification_template(
			f"_T PO Save SMS {frappe.generate_hash(length=6)}", event="Save", body="Saved {{ doc.name }}"
		)
		ctx = get_sms_context("Purchase Order", po.name)
		self.assertEqual(ctx["mobile"], "98765 43210")
		self.assertEqual(ctx["supplier"], po.supplier)
		by_name = {t["name"]: t["message"] for t in ctx["templates"]}
		self.assertEqual(by_name[t1.name], f"PO {po.name} for {po.supplier}")
		self.assertEqual(by_name[t2.name], f"Saved {po.name}")

	def test_sms_context_without_template_throws(self):
		from yrp.notification import get_sms_context

		po = self._submitted_po()
		with self.assertRaises(frappe.ValidationError):
			get_sms_context("Purchase Order", po.name)

	def test_sms_context_without_mobile_throws(self):
		from yrp.notification import get_sms_context

		po = self._submitted_po(mobile=None)
		_notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		with self.assertRaises(frappe.ValidationError):
			get_sms_context("Purchase Order", po.name)

	def test_send_sms_notification_sends_edited_message(self):
		from yrp.notification import send_sms_notification

		po = self._submitted_po()
		template = _notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		with patch(SEND_REQUEST, return_value=_gw(200)) as mock_send:
			send_sms_notification(
				"Purchase Order", po.name, template=template.name, message="Edited text for supplier"
			)
		mock_send.assert_called_once()
		self.assertEqual(mock_send.call_args.args[1]["msg"], "Edited text for supplier")
		self.assertEqual(mock_send.call_args.args[1]["to"], "9876543210")
		self.assertTrue(frappe.db.exists("Communication", {
			"reference_doctype": "Purchase Order",
			"reference_name": po.name,
			"communication_medium": "SMS",
		}))

	def test_send_sms_notification_defaults_to_rendered_template(self):
		from yrp.notification import send_sms_notification

		po = self._submitted_po()
		template = _notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		with patch(SEND_REQUEST, return_value=_gw(200)) as mock_send:
			send_sms_notification("Purchase Order", po.name, template=template.name)
		self.assertEqual(mock_send.call_args.args[1]["msg"], f"PO {po.name} for {po.supplier}")

	def test_send_sms_notification_carries_template_params(self):
		from yrp.notification import send_sms_notification

		po = self._submitted_po()
		template = _notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		template.append("parameters", {"parameter": "dlt_template_id", "value": "1107ABC"})
		template.save(ignore_permissions=True)
		with patch(SEND_REQUEST, return_value=_gw(200)) as mock_send:
			send_sms_notification("Purchase Order", po.name, template=template.name, message="x")
		self.assertEqual(mock_send.call_args.args[1]["dlt_template_id"], "1107ABC")

	def test_send_sms_notification_rejects_foreign_template(self):
		from yrp.notification import send_sms_notification

		po = self._submitted_po()
		wrong = _notification_template(
			f"_T DC SMS {frappe.generate_hash(length=6)}", document_type="Delivery Challan"
		)
		with patch(SEND_REQUEST, return_value=_gw(200)) as mock_send:
			with self.assertRaises(frappe.ValidationError):
				send_sms_notification("Purchase Order", po.name, template=wrong.name)
		mock_send.assert_not_called()

	def test_sms_context_lists_all_numbers_primary_first_comma_split(self):
		from yrp.notification import get_sms_context

		supplier = _supplier(f"_T SMS Nums {frappe.generate_hash(length=6)}")
		_contact_with_phones(supplier, [("9000000001", False), ("9000000002,9000000003", True)])
		po = _purchase_order(qty=1, warehouse=_warehouse("_T SMS WH"), supplier=supplier)
		_notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		ctx = get_sms_context("Purchase Order", po.name)
		# primary field held "9000000002,9000000003" -> split; primary number first
		self.assertEqual(ctx["numbers"][0], "9000000002")
		self.assertIn("9000000003", ctx["numbers"])
		self.assertIn("9000000001", ctx["numbers"])
		self.assertEqual(len(ctx["numbers"]), 3)

	def test_send_sms_notification_writes_log_with_request_id(self):
		from yrp.notification import send_sms_notification

		po = self._submitted_po()
		template = _notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		with patch(SEND_REQUEST, return_value=_gw(200, "REQ123")):
			send_sms_notification("Purchase Order", po.name, template=template.name,
				message="edited", mobile_no="9000000009")
		log = frappe.get_last_doc("SMS Notification Log",
			filters={"reference_doctype": "Purchase Order", "reference_name": po.name})
		self.assertEqual(log.status, "Sent")
		self.assertEqual(log.request_id, "REQ123")
		self.assertEqual(log.mobile_no, "9000000009")

	def test_send_sms_notification_logs_failure(self):
		from yrp.notification import send_sms_notification

		po = self._submitted_po()
		template = _notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		with patch(SEND_REQUEST, return_value=_gw(400, "ERR")):
			send_sms_notification("Purchase Order", po.name, template=template.name,
				message="x", mobile_no="9000000009")
		log = frappe.get_last_doc("SMS Notification Log",
			filters={"reference_doctype": "Purchase Order", "reference_name": po.name})
		self.assertEqual(log.status, "Failed")

	def test_resend_updates_the_log_row(self):
		from yrp.notification import send_sms_notification, resend_sms_notification_log

		po = self._submitted_po()
		template = _notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		with patch(SEND_REQUEST, return_value=_gw(400, "ERR")):
			send_sms_notification("Purchase Order", po.name, template=template.name,
				message="x", mobile_no="9000000009")
		log = frappe.get_last_doc("SMS Notification Log",
			filters={"reference_doctype": "Purchase Order", "reference_name": po.name})
		self.assertEqual(log.status, "Failed")
		with patch(SEND_REQUEST, return_value=_gw(200, "REQ999")):
			resend_sms_notification_log(log.name)
		log.reload()
		self.assertEqual(log.status, "Sent")
		self.assertEqual(log.request_id, "REQ999")

	def test_resend_blocks_when_reference_deleted(self):
		from yrp.notification import send_sms_notification, resend_sms_notification_log

		po = self._submitted_po()
		template = _notification_template(f"_T PO Submit SMS {frappe.generate_hash(length=6)}")
		with patch(SEND_REQUEST, return_value=_gw(200, "REQ")):
			send_sms_notification("Purchase Order", po.name, template=template.name,
				message="x", mobile_no="9000000009")
		log = frappe.get_last_doc("SMS Notification Log",
			filters={"reference_doctype": "Purchase Order", "reference_name": po.name})
		# simulate the reference doc being gone (DB-level, bypasses link checks)
		frappe.db.set_value("SMS Notification Log", log.name, "reference_name", "NONEXISTENT-PO-XYZ")
		with patch(SEND_REQUEST, return_value=_gw(200, "REQ2")) as mock_send:
			with self.assertRaises(frappe.ValidationError):
				resend_sms_notification_log(log.name)
		mock_send.assert_not_called()
