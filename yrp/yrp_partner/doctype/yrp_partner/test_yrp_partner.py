import unittest
from unittest.mock import patch

import frappe


class PartnerTestCase(unittest.TestCase):
	def setUp(self):
		self.savepoint = 'partner_' + frappe.generate_hash(length=10)
		frappe.db.savepoint(self.savepoint)
		self.addCleanup(frappe.db.rollback, save_point=self.savepoint)
		self.enterContext(patch("yrp.yrp_partner.backfill.INLINE_LIMIT", float("inf")))
		self.source = self.make_source()
		self.partner_type = frappe.get_doc({
			'doctype': 'YRP Partner Type',
			'partner_type_name': 'Test Partner ' + frappe.generate_hash(length=10),
			'reference_doctype': 'UOM',
		}).insert()

	def make_source(self):
		return frappe.get_doc({
			'doctype': 'UOM', 'uom_name': 'Test Unit ' + frappe.generate_hash(length=10),
		}).insert()

	def partner(self, source=None):
		return frappe.get_doc('YRP Partner', {
			'partner_type': self.partner_type.name,
			'reference_name': (source or self.source).name,
		})

	def make_user(self, *, email=None, partner_role=True, enabled=True, mobile_no=None):
		return frappe.get_doc({
			'doctype': 'User',
			'email': email or 'partner-' + frappe.generate_hash(length=10) + '@example.invalid',
			'first_name': 'Test Partner User', 'send_welcome_email': 0,
			'enabled': int(enabled), 'mobile_no': mobile_no,
			'roles': [{'role': 'YRP Partner'}] if partner_role else [],
		}).insert()

	def add_member(self, user, source=None):
		partner = self.partner(source)
		partner.append('users', {'user': user.name})
		partner.save()
		return partner

	def members(self, source=None):
		return [row.user for row in self.partner(source).users]

	def make_contact(self, user=None, source=None, *, emails=None):
		if emails is None:
			emails = [user.email] if user else []
		return frappe.get_doc({
			'doctype': 'Contact', 'first_name': 'Test Contact ' + frappe.generate_hash(length=10),
			'user': user.name if user else None,
			'email_ids': [{'email_id': email, 'is_primary': int(index == 0)}
				for index, email in enumerate(emails)],
			'links': [{'link_doctype': 'UOM', 'link_name': (source or self.source).name}],
		}).insert()


class TestYRPPartner(PartnerTestCase):

	def test_type_save_generates_existing_records(self):
		partner = self.partner()
		self.assertEqual(partner.reference_doctype, 'UOM')
		self.assertEqual(partner.users, [])

	def test_new_partners_use_native_pr_series(self):
		self.assertEqual(frappe.get_meta('YRP Partner').autoname, 'PR.#####')
		first = self.partner(self.make_source())
		second = self.partner(self.make_source())
		for partner in (first, second):
			# In Frappe series syntax, the dot separates the prefix and counter.
			self.assertRegex(partner.name, r'^PR\d{5,}$')
			self.assertEqual(frappe.db.count('YRP Partner', {'name': partner.name}), 1)
		self.assertNotEqual(first.name, second.name)
		self.assertGreater(int(second.name[2:]), int(first.name[2:]))

	def test_resync_preserves_legacy_partner_name(self):
		# Seed an owned legacy record using the former naming metadata only
		# inside this transaction; resync must not rename it to the new series.
		with patch.object(frappe.get_meta('YRP Partner'), 'autoname', 'hash'):
			source = self.make_source()
		legacy_name = self.partner(source).name
		self.assertNotRegex(legacy_name, r'^PR\d{5,}$')
		source.save()
		self.partner_type.save()
		self.assertEqual(self.partner(source).name, legacy_name)
		self.assertEqual(frappe.db.count('YRP Partner', {
			'partner_type': self.partner_type.name, 'reference_name': source.name,
		}), 1)

	def test_source_insert_and_repeated_updates_are_idempotent(self):
		source = self.make_source()
		name = self.partner(source).name
		series = frappe.qb.DocType('Series')
		counter = frappe.qb.from_(series).select(series.current).where(series.name == 'PR')
		series_before = counter.run()[0][0]
		source.save()
		self.partner_type.save()
		self.assertEqual(self.partner(source).name, name)
		self.assertEqual(counter.run()[0][0], series_before)
		self.assertEqual(frappe.db.count('YRP Partner', {
			'partner_type': self.partner_type.name, 'reference_name': source.name,
		}), 1)

	def test_update_recreates_missing_partner(self):
		# Simulate a missing derived record; this test exercises regeneration.
		frappe.db.delete('YRP Partner', {'name': self.partner().name})
		self.source.save()
		self.assertTrue(self.partner().name)

	def test_linked_contact_emails_never_add_members(self):
		self.make_contact(self.make_user())
		self.source.save()
		self.partner_type.save()
		self.assertEqual(self.members(), [])

	def test_contact_changes_keep_explicit_members(self):
		user = self.make_user()
		self.add_member(user)
		contact = self.make_contact(user)
		other = self.make_source()
		contact.links[0].link_name = other.name
		contact.save()
		contact.delete()
		self.assertEqual(self.members(), [user.name])
		self.assertEqual(self.members(other), [])

	def test_manual_membership_is_saved_once(self):
		user = self.make_user()
		partner = self.add_member(user)
		self.assertEqual(self.members(), [user.name])
		partner.append('users', {'user': user.name})
		with self.assertRaises(frappe.ValidationError):
			partner.save()

	def test_reference_type_cannot_be_reassigned(self):
		self.partner_type.reference_doctype = 'Item Group'
		with self.assertRaises(frappe.ValidationError):
			self.partner_type.save()

	def test_child_single_and_partner_types_are_rejected(self):
		for doctype in ('Sales Order Item', 'System Settings', 'YRP Partner'):
			with self.subTest(doctype=doctype):
				with self.assertRaises(frappe.ValidationError):
					frappe.get_doc({
						'doctype': 'YRP Partner Type',
						'partner_type_name': 'Test Invalid ' + frappe.generate_hash(length=10),
						'reference_doctype': doctype,
					}).insert()

	def test_manual_partner_creation_is_rejected(self):
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc({
				'doctype': 'YRP Partner', 'partner_type': self.partner_type.name,
				'reference_doctype': 'UOM', 'reference_name': self.source.name,
			}).insert(ignore_permissions=True)

	def test_type_backfill_creates_partners_without_members(self):
		self.make_contact(self.make_user())
		partner_type = frappe.get_doc({
			"doctype": "YRP Partner Type",
			"partner_type_name": "Test Backfill " + frappe.generate_hash(length=10),
			"reference_doctype": "UOM",
		}).insert()
		partner = frappe.get_doc("YRP Partner", {
			"partner_type": partner_type.name, "reference_name": self.source.name,
		})
		self.assertEqual(partner.users, [])
