"""Local-only membership repair; never creates accounts or changes Contact identity."""

import unittest
from unittest.mock import patch

import frappe

from yrp.yrp_partner.sync import ensure_source_membership


class TestSourceMembership(unittest.TestCase):
	def setUp(self):
		self.point = "local_membership_" + frappe.generate_hash(length=8)
		frappe.db.savepoint(self.point)
		self.addCleanup(frappe.db.rollback, save_point=self.point)
		self.addCleanup(frappe.set_user, frappe.session.user)
		frappe.set_user("Administrator")
		self.enterContext(patch.dict(frappe.flags, mute_emails=True, in_import=True))
		self.source = frappe.get_doc({
			"doctype": "UOM", "enabled": 1, "uom_name": "_LOCAL Membership " + frappe.generate_hash(length=10),
		}).insert()
		self.user = frappe.get_doc({
			"doctype": "User", "first_name": "Local Membership",
			"email": "local-membership-" + frappe.generate_hash(length=10) + "@example.invalid",
			"send_welcome_email": 0,
		}).insert(ignore_permissions=True)
		# Simulate an imported source/Type for which the generator hook did not run.
		with patch("yrp.yrp_partner.backfill.sync_partner_type"):
			self.kind = frappe.get_doc({
				"doctype": "YRP Partner Type", "reference_doctype": "UOM",
				"partner_type_name": "_LOCAL Membership " + frappe.generate_hash(length=10),
			}).insert()

	def test_missing_partner_and_membership_are_repaired_idempotently(self):
		count = frappe.db.count("User")
		for _ in range(2):
			ensure_source_membership("UOM", self.source.name, self.user.name)
		partner = frappe.get_doc("YRP Partner", {"partner_type": self.kind.name, "reference_name": self.source.name})
		self.assertEqual([row.user for row in partner.users], [self.user.name])
		self.assertIn("YRP Partner", frappe.get_roles(self.user.name))
		self.assertEqual(frappe.db.count("User"), count)

	def test_repair_does_not_enable_a_disabled_user(self):
		self.user.enabled = 0
		self.user.save()
		ensure_source_membership("UOM", self.source.name, self.user.name)
		self.assertFalse(self.user.reload().enabled)

	def test_disabled_source_is_rejected(self):
		self.source.enabled = 0
		self.source.save()
		with self.assertRaisesRegex(frappe.ValidationError, "Inactive"):
			ensure_source_membership("UOM", self.source.name, self.user.name)

	def test_disabled_partner_is_not_reenabled(self):
		from yrp.yrp_partner.sync import ensure_partner

		ensure_partner(self.kind.name, "UOM", self.source.name)
		partner = frappe.get_doc("YRP Partner", {"partner_type": self.kind.name, "reference_name": self.source.name})
		partner.disabled = 1
		partner.save()
		with self.assertRaisesRegex(frappe.ValidationError, "disabled"):
			ensure_source_membership("UOM", self.source.name, self.user.name)
		self.assertEqual(partner.reload().users, [])
		self.assertTrue(partner.disabled)

	def test_missing_source_and_staff_are_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Inactive"):
			ensure_source_membership("UOM", "_LOCAL Missing Source", self.user.name)
		self.user.append_roles("Sales User")
		self.user.save()
		with self.assertRaisesRegex(frappe.ValidationError, "staff"):
			ensure_source_membership("UOM", self.source.name, self.user.name)

	def test_missing_configuration_is_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "No Partner Type"):
			ensure_source_membership("Role", "Guest", self.user.name)
