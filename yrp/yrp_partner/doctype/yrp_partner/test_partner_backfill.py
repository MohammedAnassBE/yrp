"""Existing Contacts must survive bulk provisioning and native User callbacks."""

import unittest
from unittest.mock import patch

import frappe


class TestPartnerBackfill(unittest.TestCase):
	def setUp(self):
		self.savepoint = "partner_backfill_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(self.savepoint)
		self.addCleanup(frappe.db.rollback, save_point=self.savepoint)
		# Identity tests execute synchronously regardless of the site's UOM count;
		# the dedicated queue tests below override this limit explicitly.
		self.enterContext(patch("yrp.yrp_partner.backfill.INLINE_LIMIT", float("inf")))
		self.source = frappe.get_doc({
			"doctype": "UOM", "uom_name": "Test Backfill " + frappe.generate_hash(length=10),
		}).insert()

	def make_contact(self, email=True):
		"""Seed a linked Contact before its source is configured as a Partner Type."""
		return frappe.get_doc({
			"doctype": "Contact", "first_name": "Test Backfill Contact " + frappe.generate_hash(length=8),
			"email_ids": [{"email_id": "backfill-" + frappe.generate_hash(length=12) + "@example.invalid", "is_primary": 1}] if email else [],
			"phone_nos": [{"phone": "1555" + str(int(frappe.generate_hash(length=8), 36) % 10_000_000).zfill(7), "is_primary_mobile_no": 1}],
			"links": [{"link_doctype": "UOM", "link_name": self.source.name}],
		}).insert()

	def make_type(self):
		return frappe.get_doc({
			"doctype": "YRP Partner Type",
			"partner_type_name": "Test Backfill Type " + frappe.generate_hash(length=10),
			"reference_doctype": "UOM", "generate_email_when_missing": 1,
			"generated_email_format": "{hash}+{mobile_no}@example.invalid",
		}).insert()

	def assert_membership(self, partner_type, contact):
		partner = frappe.get_doc("YRP Partner", {
			"partner_type": partner_type.name, "reference_name": self.source.name,
		})
		self.assertIn(contact.user, [row.user for row in partner.users])
		self.assertIn("YRP Partner", frappe.get_roles(contact.user))

	def test_existing_email_contact_survives_native_user_callback(self):
		contact = self.make_contact()
		self.assertFalse(contact.user)
		partner_type = self.make_type()
		contact.reload()
		self.assertEqual(contact.user, contact.email_ids[0].email_id)
		self.assert_membership(partner_type, contact)
		modified = contact.modified
		partner_type.save()
		contact.reload()
		self.assertEqual(contact.modified, modified)
		self.assertEqual(frappe.db.count("User", {"email": contact.user}), 1)

	def test_bulk_backfill_does_not_disable_normal_creation_throttle(self):
		contact = self.make_contact()
		original_import_flag = frappe.flags.in_import
		limit = frappe.conf.get("throttle_user_limit", 60)
		with patch.object(frappe.db, "get_creation_count", return_value=limit + 1):
			partner_type = self.make_type()
			contact.reload()
			self.assert_membership(partner_type, contact)
			self.assertEqual(frappe.flags.in_import, original_import_flag)
			with self.assertRaises(frappe.ValidationError):
				frappe.get_doc({
					"doctype": "User", "first_name": "Test Ordinary User",
					"email": "ordinary-" + frappe.generate_hash(length=12) + "@example.invalid",
					"send_welcome_email": 0,
					"flags": {"yrp_provisioning_token": True},
				}).insert()

	def test_provisioning_restores_import_flag_on_failure(self):
		self.make_contact()
		original_import_flag = frappe.flags.in_import
		with patch("frappe.core.doctype.user.user.User.before_insert", side_effect=frappe.ValidationError("Test failure")):
			with self.assertRaisesRegex(frappe.ValidationError, "Test failure"):
				self.make_type()
		self.assertEqual(frappe.flags.in_import, original_import_flag)

	def test_existing_mobile_contact_gets_one_stable_generated_identity(self):
		contact = self.make_contact(email=False)
		partner_type = self.make_type()
		contact.reload()
		user = contact.user
		self.assertTrue(user)
		self.assertEqual(contact.email_ids[0].email_id, user)
		self.assert_membership(partner_type, contact)
		partner_type.save()
		contact.reload()
		self.assertEqual(contact.user, user)
		self.assertEqual(frappe.db.count("User", {"mobile_no": contact.phone_nos[0].phone}), 1)

	def test_real_stale_contact_update_is_still_rejected(self):
		contact = self.make_contact()
		self.make_type()
		contact.reload()
		stale = frappe.get_doc("Contact", contact.name)
		contact.designation = "Updated role"
		contact.save()
		stale.designation = "Outdated role"
		with self.assertRaises(frappe.TimestampMismatchError):
			stale.save()

	def test_large_backfill_queues_then_worker_provisions_contacts(self):
		from yrp.yrp_partner import backfill

		contact = self.make_contact()
		with patch.object(backfill, "INLINE_LIMIT", 0), patch.object(frappe, "enqueue") as enqueue:
			partner_type = self.make_type()
			self.assertFalse(contact.reload().user)
			self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])
			self.assertEqual(enqueue.call_args.kwargs["partner_type"], partner_type.name)
		with patch.object(backfill, "BATCH_SIZE", 1000):
			backfill.run_batch(partner_type.name, str(partner_type.modified))
		contact.reload()
		self.assert_membership(partner_type, contact)

	def test_superseded_backfill_does_not_create_users(self):
		from yrp.yrp_partner import backfill

		contact = self.make_contact()
		with patch.object(backfill, "INLINE_LIMIT", 0), patch.object(frappe, "enqueue"):
			partner_type = self.make_type()
		backfill.run_batch(partner_type.name, "obsolete revision")
		self.assertFalse(contact.reload().user)

	def test_batch_chains_only_after_its_transaction_commits(self):
		from yrp.yrp_partner import backfill

		with patch.object(backfill, "INLINE_LIMIT", 0), patch.object(frappe, "enqueue"):
			partner_type = self.make_type()
		with patch.object(backfill, "BATCH_SIZE", 1), patch.object(frappe, "enqueue") as enqueue:
			backfill.run_batch(partner_type.name, str(partner_type.modified))
			self.assertEqual(enqueue.call_count, 1)
			self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])
			self.assertTrue(enqueue.call_args.kwargs["cursor"])
			self.assertEqual(enqueue.call_args.kwargs["processed"], 1)
			self.assertEqual(enqueue.call_args.kwargs["requested_by"], frappe.session.user)

	def test_background_retry_rechecks_the_original_requester(self):
		from yrp.yrp_partner import backfill

		contact = self.make_contact()
		with patch.object(backfill, "INLINE_LIMIT", 0), patch.object(frappe, "enqueue"):
			partner_type = self.make_type()
		user = frappe.get_doc({"doctype": "User", "first_name": "Test Unprivileged",
			"email": "requester-" + frappe.generate_hash(length=12) + "@example.invalid",
			"send_welcome_email": 0}).insert()
		original_user = frappe.session.user
		try:
			with self.assertRaises(frappe.PermissionError):
				backfill.run_batch(partner_type.name, str(partner_type.modified), requested_by=user.name)
			self.assertFalse(contact.reload().user)
		finally:
			frappe.set_user(original_user)
