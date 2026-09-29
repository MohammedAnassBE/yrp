"""Backfills derive existing email memberships without changing Contacts or Users."""

import unittest
from unittest.mock import patch

import frappe


class TestPartnerBackfill(unittest.TestCase):
	def setUp(self):
		self.savepoint = "partner_backfill_" + frappe.generate_hash(length=10)
		frappe.db.savepoint(self.savepoint)
		self.addCleanup(frappe.db.rollback, save_point=self.savepoint)
		self.addCleanup(frappe.set_user, frappe.session.user)
		# Membership tests stay synchronous; queue tests override this limit.
		self.enterContext(patch("yrp.yrp_partner.backfill.INLINE_LIMIT", float("inf")))
		self.source = frappe.get_doc({
			"doctype": "UOM", "uom_name": "Test Backfill " + frappe.generate_hash(length=10),
		}).insert()

	def make_contact(self, emails=None):
		"""Seed a linked Contact before creating this test's Partner Type."""
		if emails is None:
			emails = ["backfill-" + frappe.generate_hash(length=12) + "@example.invalid"]
		return frappe.get_doc({
			"doctype": "Contact", "first_name": "Test Backfill Contact " + frappe.generate_hash(length=8),
			"email_ids": [{"email_id": email, "is_primary": int(index == 0)}
				for index, email in enumerate(emails)],
			"phone_nos": [{"phone": "1555" + str(int(frappe.generate_hash(length=8), 36) % 10_000_000).zfill(7), "is_primary_mobile_no": 1}],
			"links": [{"link_doctype": "UOM", "link_name": self.source.name}],
		}).insert()

	def make_user(self, *, partner_role=True, enabled=True):
		return frappe.get_doc({
			"doctype": "User", "first_name": "Test Backfill User",
			"email": "backfill-user-" + frappe.generate_hash(length=12) + "@example.invalid",
			"send_welcome_email": 0, "enabled": int(enabled),
			"roles": [{"role": "YRP Partner"}] if partner_role else [],
		}).insert()

	def make_type(self):
		return frappe.get_doc({
			"doctype": "YRP Partner Type",
			"partner_type_name": "Test Backfill Type " + frappe.generate_hash(length=10),
			"reference_doctype": "UOM",
		}).insert()

	def assert_membership(self, partner_type, users):
		partner = frappe.get_doc("YRP Partner", {
			"partner_type": partner_type.name, "reference_name": self.source.name,
		})
		self.assertEqual({row.user for row in partner.users}, {user.name for user in users})

	def test_backfill_leaves_unknown_email_and_mobile_contacts_unchanged(self):
		contacts = [self.make_contact(), self.make_contact(emails=[])]
		before = {contact.name: contact.reload().as_dict() for contact in contacts}
		user_count = frappe.db.count("User")
		original_import_flag = frappe.flags.in_import
		with patch("frappe.core.doctype.user.user.User.before_insert", side_effect=AssertionError("Unexpected User creation")):
			partner_type = self.make_type()
			partner_type.save()
		self.assert_membership(partner_type, [])
		self.assertEqual(frappe.db.count("User"), user_count)
		self.assertEqual(frappe.flags.in_import, original_import_flag)
		for contact in contacts:
			self.assertEqual(contact.reload().as_dict(), before[contact.name])

	def test_backfill_uses_only_enabled_existing_users_with_partner_role(self):
		active = self.make_user()
		disabled = self.make_user(enabled=False)
		without_role = self.make_user(partner_role=False)
		users = (active, disabled, without_role)
		contacts = [self.make_contact(emails=[user.email]) for user in users]
		contact_snapshots = {contact.name: contact.reload().as_dict() for contact in contacts}
		user_snapshots = {user.name: user.reload().as_dict() for user in users}
		user_count = frappe.db.count("User")
		partner_type = self.make_type()
		for _ in range(2):
			partner_type.save()
			self.assert_membership(partner_type, [active])
			self.assertEqual(frappe.db.count("User"), user_count)
			for contact in contacts:
				self.assertEqual(contact.reload().as_dict(), contact_snapshots[contact.name])
			for user in users:
				self.assertEqual(user.reload().as_dict(), user_snapshots[user.name])

	def test_backfill_matches_secondary_email_without_populating_contact_user(self):
		user = self.make_user()
		contact = self.make_contact(emails=[
			"unmatched-" + frappe.generate_hash(length=12) + "@example.invalid", user.email,
		])
		self.assertFalse(contact.user)
		before = contact.reload().as_dict()
		partner_type = self.make_type()
		self.assert_membership(partner_type, [user])
		self.assertEqual(contact.reload().as_dict(), before)

	def test_real_stale_contact_update_is_still_rejected(self):
		contact = self.make_contact()
		self.make_type()
		stale = frappe.get_doc("Contact", contact.name)
		contact.designation = "Updated role"
		contact.save()
		stale.designation = "Outdated role"
		with self.assertRaises(frappe.TimestampMismatchError):
			stale.save()

	def test_large_backfill_queues_then_worker_derives_membership(self):
		from yrp.yrp_partner import backfill

		user = self.make_user()
		contact = self.make_contact(emails=[user.email])
		contact_before, user_before = contact.reload().as_dict(), user.reload().as_dict()
		with patch.object(backfill, "INLINE_LIMIT", 0), patch.object(frappe, "enqueue") as enqueue:
			partner_type = self.make_type()
			self.assertFalse(frappe.db.exists("YRP Partner", {"partner_type": partner_type.name}))
			self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])
			self.assertEqual(enqueue.call_args.kwargs["partner_type"], partner_type.name)
		with patch.object(backfill, "BATCH_SIZE", frappe.db.count("UOM") + 1), patch(
			"frappe.contacts.doctype.contact.contact.Contact.save", side_effect=AssertionError("Unexpected Contact update")
		), patch("frappe.core.doctype.user.user.User.save", side_effect=AssertionError("Unexpected User update")):
			backfill.run_batch(partner_type.name, str(partner_type.modified))
		self.assert_membership(partner_type, [user])
		self.assertEqual(contact.reload().as_dict(), contact_before)
		self.assertEqual(user.reload().as_dict(), user_before)

	def test_superseded_backfill_does_not_generate_partners_or_change_contacts(self):
		from yrp.yrp_partner import backfill

		contact = self.make_contact()
		before = contact.reload().as_dict()
		user_count = frappe.db.count("User")
		with patch.object(backfill, "INLINE_LIMIT", 0), patch.object(frappe, "enqueue"):
			partner_type = self.make_type()
		backfill.run_batch(partner_type.name, "obsolete revision")
		self.assertFalse(frappe.db.exists("YRP Partner", {"partner_type": partner_type.name}))
		self.assertEqual(contact.reload().as_dict(), before)
		self.assertEqual(frappe.db.count("User"), user_count)

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
		before = contact.reload().as_dict()
		with patch.object(backfill, "INLINE_LIMIT", 0), patch.object(frappe, "enqueue"):
			partner_type = self.make_type()
		user = self.make_user(partner_role=False)
		with self.assertRaises(frappe.PermissionError):
			backfill.run_batch(partner_type.name, str(partner_type.modified), requested_by=user.name)
		self.assertFalse(frappe.db.exists("YRP Partner", {"partner_type": partner_type.name}))
		self.assertEqual(contact.reload().as_dict(), before)

	def test_disabled_requester_cannot_run_backfill(self):
		from yrp.yrp_partner import backfill

		with patch.object(backfill, "INLINE_LIMIT", 0), patch.object(frappe, "enqueue"):
			partner_type = self.make_type()
		user = self.make_user(partner_role=False, enabled=False)
		with self.assertRaisesRegex(frappe.PermissionError, "disabled"):
			backfill.run_batch(partner_type.name, str(partner_type.modified), requested_by=user.name)
		self.assertFalse(frappe.db.exists("YRP Partner", {"partner_type": partner_type.name}))
