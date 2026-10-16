from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from yrp.yrp.doctype.yrp_debit import yrp_debit
from yrp.yrp.doctype.yrp_purchase_invoice.test_yrp_purchase_invoice import (
	_work_order_for_invoice,
)


class TestYRPDebit(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.roles = {
			"debit_request_role": "System Manager",
			"debit_approval_role": "System Manager",
			"work_order_closing_approver_role": "System Manager",
		}
		get_single_value = frappe.db.get_single_value

		def settings_value(doctype, fieldname, *args, **kwargs):
			if doctype == "YRP Settings" and fieldname in self.roles:
				return self.roles[fieldname]
			return get_single_value(doctype, fieldname, *args, **kwargs)

		patcher = patch.object(frappe.db, "get_single_value", side_effect=settings_value)
		patcher.start()
		self.addCleanup(patcher.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def create_debit(self, work_order, **overrides):
		values = {
			"debit_no": f"DEBIT-{frappe.generate_hash(length=6)}",
			"debit_value": 25.5,
			"reason": "Short receipt",
			"on_close": 0,
		}
		values.update(overrides)
		return yrp_debit.create_debit(work_order, **values)

	def set_unprivileged_user(self):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"debit-{frappe.generate_hash(length=8)}@example.com",
				"first_name": "Debit Permission Test",
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		).insert(ignore_permissions=True)
		frappe.set_user(user.name)
		self.assertNotIn("System Manager", frappe.get_roles())

	def test_allowed_user_creates_submitted_debit_with_attachment_and_approval_status(self):
		wo = _work_order_for_invoice(qty=1)
		self.assertTrue(yrp_debit.has_debit_request_permission())
		for approval_role, status, approved_by in (
			("System Manager", "Approved", "Administrator"),
			(None, "Debit Requested", None),
		):
			with self.subTest(status=status):
				self.roles["debit_approval_role"] = approval_role
				attachment = frappe.get_doc(
					{
						"doctype": "File",
						"file_name": f"debit-{frappe.generate_hash(length=8)}.txt",
						"content": f"Debit supporting document {frappe.generate_hash(length=8)}",
						"is_private": 1,
					}
				).insert(ignore_permissions=True)
				result = self.create_debit(
					wo.name, debit_no="D-1", debit_document=attachment.file_url
				)
				debit = frappe.get_doc("YRP Debit", result.name)
				for field, expected in {
					"work_order": wo.name,
					"debit_type": "Permanent",
					"debit_no": "D-1",
					"debit_value": 25.5,
					"reason": "Short receipt",
					"debit_document": attachment.file_url,
					"on_close": 0,
					"inspection": 0,
					"docstatus": 1,
					"status": status,
					"approved_by": approved_by,
				}.items():
					self.assertEqual(debit.get(field), expected, field)
				self.assertEqual(result.docstatus, 1)
				attachment.reload()
				self.assertEqual(attachment.attached_to_doctype, "YRP Debit")
				self.assertEqual(attachment.attached_to_name, debit.name)
				self.assertEqual(attachment.attached_to_field, "debit_document")

	def test_permission_denied_when_request_role_is_blank(self):
		self.roles["debit_request_role"] = None
		self.assertFalse(yrp_debit.has_debit_request_permission())

	def test_permission_denied_when_user_lacks_request_role(self):
		self.set_unprivileged_user()
		self.assertFalse(yrp_debit.has_debit_request_permission())

	def test_permission_requires_both_create_and_submit(self):
		for denied in ("create", "submit"):
			with self.subTest(denied=denied), patch.object(
				frappe, "has_permission", side_effect=lambda _dt, ptype: ptype != denied
			):
				self.assertFalse(yrp_debit.has_debit_request_permission())

	def test_create_rejects_user_without_request_permission(self):
		wo = _work_order_for_invoice(qty=1)
		self.set_unprivileged_user()
		with self.assertRaises(frappe.PermissionError):
			self.create_debit(wo.name)

	def test_create_rejects_missing_or_non_open_submitted_work_order(self):
		wo = _work_order_for_invoice(qty=1)
		for docstatus, open_status in ((0, "Open"), (2, "Open"), (1, "Close"), (1, "Close Request")):
			with self.subTest(docstatus=docstatus, open_status=open_status):
				frappe.db.set_value(
					"YRP Work Order", wo.name, {"docstatus": docstatus, "open_status": open_status}
				)
				with self.assertRaisesRegex(frappe.ValidationError, "open submitted Work Order"):
					self.create_debit(wo.name)
		with self.assertRaisesRegex(frappe.ValidationError, "open submitted Work Order"):
			self.create_debit("MISSING-WORK-ORDER")

	def test_create_rejects_non_positive_value_on_both_paths(self):
		wo = _work_order_for_invoice(qty=1)
		for on_close in (0, 1):
			for value in (0, -1):
				with self.subTest(on_close=on_close, value=value), self.assertRaisesRegex(
					frappe.ValidationError, "Debit Value must be greater than zero"
				):
					self.create_debit(wo.name, debit_value=value, on_close=on_close)

	def test_create_rejects_blank_reason_on_both_paths(self):
		wo = _work_order_for_invoice(qty=1)
		for on_close in (0, 1):
			with self.subTest(on_close=on_close), self.assertRaisesRegex(
				frappe.ValidationError, "Reason is required"
			):
				self.create_debit(wo.name, reason=" \n ", on_close=on_close)

	def test_create_rejects_blank_debit_no(self):
		wo = _work_order_for_invoice(qty=1)
		with self.assertRaisesRegex(frappe.ValidationError, "Debit No is required"):
			self.create_debit(wo.name, debit_no="")

	def test_close_path_keeps_closing_role_permission_and_allows_close_request(self):
		wo = _work_order_for_invoice(qty=1)
		frappe.db.set_value("YRP Work Order", wo.name, "open_status", "Close Request")
		self.roles["debit_request_role"] = None
		with patch.object(frappe, "has_permission", return_value=False):
			result = self.create_debit(wo.name, on_close=1)
		self.assertEqual(result.docstatus, 1)
		self.assertEqual(result.on_close, 1)

	def test_close_path_rejects_user_without_closing_role(self):
		self.roles["work_order_closing_approver_role"] = None
		with self.assertRaises(frappe.ValidationError):
			self.create_debit("WO-1", on_close=1)
