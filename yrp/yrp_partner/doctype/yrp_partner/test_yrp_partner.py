import unittest
from unittest.mock import patch

import frappe


class TestYRPPartner(unittest.TestCase):
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

	def test_type_save_generates_existing_records(self):
		partner = self.partner()
		self.assertEqual(partner.reference_doctype, 'UOM')
		self.assertEqual(partner.users, [])

	def test_source_insert_and_repeated_updates_are_idempotent(self):
		source = self.make_source()
		name = self.partner(source).name
		source.save()
		self.partner_type.save()
		self.assertEqual(self.partner(source).name, name)
		self.assertEqual(frappe.db.count('YRP Partner', {
			'partner_type': self.partner_type.name, 'reference_name': source.name,
		}), 1)

	def test_update_recreates_missing_partner(self):
		# Simulate a missing derived record; this test exercises regeneration.
		frappe.db.delete('YRP Partner', {'name': self.partner().name})
		self.source.save()
		self.assertTrue(self.partner().name)

	def make_user(self):
		return frappe.get_doc({
			'doctype': 'User',
			'email': 'partner-' + frappe.generate_hash(length=10) + '@example.invalid',
			'first_name': 'Test Partner User', 'send_welcome_email': 0,
		}).insert()

	def make_contact(self, user=None, source=None):
		return frappe.get_doc({
			'doctype': 'Contact', 'first_name': 'Test Contact ' + frappe.generate_hash(length=10),
			'user': user.name if user else None,
			'links': [{'link_doctype': 'UOM', 'link_name': (source or self.source).name}],
		}).insert()

	def test_linked_contacts_generate_unique_users(self):
		user = self.make_user()
		self.make_contact(user)
		self.make_contact(user)
		self.make_contact()
		self.source.save()
		self.partner_type.save()
		self.assertEqual([r.user for r in self.partner().users], [user.name])

	def test_contact_reassignment_refreshes_both_partners(self):
		user = self.make_user()
		contact = self.make_contact(user)
		other = self.make_source()
		contact.links[0].link_name = other.name
		contact.save()
		self.assertEqual(self.partner().users, [])
		self.assertEqual([r.user for r in self.partner(other).users], [user.name])

	def test_contact_deletion_hook_removes_user(self):
		contact = self.make_contact(self.make_user())
		# Exercise the registered lifecycle hook without scanning unrelated historical links.
		contact.run_method('on_trash')
		self.assertEqual(self.partner().users, [])

	def test_manual_membership_is_rejected(self):
		partner = self.partner()
		partner.append('users', {'user': self.make_user().name})
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


	def test_contact_user_change_replaces_membership(self):
		contact = self.make_contact(self.make_user())
		replacement = self.make_user()
		contact.user = replacement.name
		contact.save()
		self.assertEqual([r.user for r in self.partner().users], [replacement.name])

	def test_type_backfill_uses_existing_contacts(self):
		user = self.make_user()
		self.make_contact(user)
		partner_type = frappe.get_doc({
			"doctype": "YRP Partner Type",
			"partner_type_name": "Test Backfill " + frappe.generate_hash(length=10),
			"reference_doctype": "UOM",
		}).insert()
		partner = frappe.get_doc("YRP Partner", {
			"partner_type": partner_type.name, "reference_name": self.source.name,
		})
		self.assertEqual([r.user for r in partner.users], [user.name])

	def test_unlinking_contact_removes_membership(self):
		contact = self.make_contact(self.make_user())
		contact.set("links", [])
		contact.save()
		self.assertEqual(self.partner().users, [])

	def test_deleting_one_of_two_contacts_keeps_shared_user(self):
		user = self.make_user()
		contact = self.make_contact(user)
		self.make_contact(user)
		contact.run_method("on_trash")
		self.assertEqual([r.user for r in self.partner().users], [user.name])
