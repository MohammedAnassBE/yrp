"""Partner membership is explicit; yrp never derives it or manages accounts."""

from unittest.mock import patch

import frappe

from yrp.yrp_partner.doctype.yrp_partner.test_yrp_partner import PartnerTestCase


class TestPartnerUsers(PartnerTestCase):
	def email(self):
		return "test-" + frappe.generate_hash(length=12) + "@example.invalid"

	def phone(self):
		return "1555" + str(int(frappe.generate_hash(length=8), 36) % 10_000_000).zfill(7)

	def resync(self):
		self.source.save()
		self.partner_type.save()

	def test_contact_email_and_mobile_never_create_users_or_members(self):
		phone = self.phone()
		user = self.make_user(mobile_no=phone)
		before = user.reload().as_dict()
		user_count = frappe.db.count("User")
		with patch("frappe.core.doctype.user.user.User.before_insert", side_effect=AssertionError("Unexpected User creation")):
			contact = self.make_contact(user, emails=[user.email, self.email()])
			contact.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
			contact.save()
			self.resync()
		self.assertEqual(self.members(), [])
		self.assertEqual(frappe.db.count("User"), user_count)
		self.assertEqual(user.reload().as_dict(), before)

	def test_explicit_members_survive_contact_source_and_type_saves(self):
		user = self.make_user()
		self.add_member(user)
		contact = self.make_contact(user)
		contact.set("email_ids", [])
		contact.save()
		self.resync()
		self.assertEqual(self.members(), [user.name])

	def test_role_and_enabled_changes_never_edit_members(self):
		user = self.make_user()
		self.add_member(user)
		user.enabled = 0
		user.set("roles", [])
		user.save()
		self.resync()
		self.assertEqual(self.members(), [user.name])

	def test_one_user_can_be_a_member_of_several_sources(self):
		user = self.make_user()
		other = self.make_source()
		self.add_member(user)
		self.add_member(user, other)
		self.assertEqual(self.members(), [user.name])
		self.assertEqual(self.members(other), [user.name])

	def test_user_rename_moves_the_member_row(self):
		user = self.make_user()
		self.add_member(user)
		new_email = self.email()

		def persist_renamed_email(doc, old_name, new_name, merge=False):
			frappe.db.set_value("User", new_name, "email", new_name)

		# Native User.after_rename scans every table's ownership history, even
		# for a synthetic user. Skip only that scan; Link renaming still runs.
		with patch("frappe.core.doctype.user.user.User.after_rename", new=persist_renamed_email):
			frappe.rename_doc("User", user.name, new_email, force=True, show_alert=False)
		self.assertEqual(self.members(), [new_email])
