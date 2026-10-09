"""Partner membership uses existing email identities without managing accounts."""

from unittest.mock import patch

import frappe

from yrp.yrp_partner.doctype.yrp_partner.test_yrp_partner import PartnerTestCase


class TestPartnerUsers(PartnerTestCase):
	def email(self):
		return "test-" + frappe.generate_hash(length=12) + "@example.invalid"

	def phone(self):
		return "1555" + str(int(frappe.generate_hash(length=8), 36) % 10_000_000).zfill(7)

	def members(self, source=None):
		return {row.user for row in self.partner(source).users}

	def test_email_and_mobile_without_existing_user_do_not_provision(self):
		email, phone = self.email(), self.phone()
		user_count = frappe.db.count("User")
		with patch("frappe.core.doctype.user.user.User.before_insert", side_effect=AssertionError("Unexpected User creation")):
			contact = self.make_contact(emails=[email])
			contact.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
			contact.save()
			self.source.save()
			self.partner_type.save()
		self.assertEqual(frappe.db.count("User"), user_count)
		self.assertFalse(contact.reload().user)
		self.assertEqual([row.email_id for row in contact.email_ids], [email])
		self.assertEqual(self.members(), set())

	def test_mobile_only_contact_does_not_generate_email_or_match_user(self):
		phone = self.phone()
		user = self.make_user(mobile_no=phone)
		before = user.reload().as_dict()
		user_count = frappe.db.count("User")
		contact = self.make_contact()
		contact.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
		contact.save()
		self.source.save()
		self.partner_type.save()
		self.assertFalse(contact.reload().user)
		self.assertEqual(contact.email_ids, [])
		self.assertEqual(self.members(), set())
		self.assertEqual(frappe.db.count("User"), user_count)
		self.assertEqual(user.reload().as_dict(), before)

	def test_contact_user_without_matching_email_does_not_grant_membership(self):
		user = self.make_user()
		contact = self.make_contact(user, emails=[])
		self.assertEqual(contact.user, user.name)
		self.assertEqual(self.members(), set())
		contact.append("email_ids", {"email_id": self.email(), "is_primary": 1})
		contact.save()
		self.assertEqual(self.members(), set())

	def test_existing_user_without_partner_role_is_unchanged_and_excluded(self):
		user = self.make_user(partner_role=False, mobile_no=self.phone())
		before = user.reload().as_dict()
		contact = self.make_contact(user)
		contact.append("phone_nos", {"phone": self.phone(), "is_primary_mobile_no": 1})
		contact.save()
		self.source.save()
		self.partner_type.save()
		self.assertEqual(self.members(), set())
		self.assertEqual(user.reload().as_dict(), before)
		self.assertNotIn("YRP Partner", [row.role for row in user.roles])

	def test_disabled_existing_user_is_unchanged_and_excluded(self):
		user = self.make_user(enabled=False, mobile_no=self.phone())
		before = user.reload().as_dict()
		contact = self.make_contact(user)
		contact.append("phone_nos", {"phone": self.phone(), "is_primary_mobile_no": 1})
		contact.save()
		self.source.save()
		self.partner_type.save()
		self.assertEqual(self.members(), set())
		self.assertEqual(user.reload().as_dict(), before)

	def test_all_matching_contact_emails_grant_existing_users(self):
		primary, secondary = self.make_user(), self.make_user()
		contact = self.make_contact(primary, emails=[primary.email, secondary.email])
		self.assertEqual(contact.user, primary.name)
		self.assertEqual(self.members(), {primary.name, secondary.name})
		contact.remove(contact.email_ids[1])
		contact.save()
		self.assertEqual(self.members(), {primary.name})

	def test_email_replacement_refreshes_membership_despite_stale_contact_user(self):
		previous, replacement = self.make_user(), self.make_user()
		contact = self.make_contact(previous)
		contact.email_ids[0].email_id = replacement.email
		contact.save()
		self.assertEqual(contact.user, previous.name)
		self.assertEqual(self.members(), {replacement.name})

	def test_removing_and_restoring_email_never_disables_or_updates_account(self):
		user = self.make_user(mobile_no=self.phone())
		contact = self.make_contact(user)
		before = user.reload().as_dict()
		contact.set("email_ids", [])
		contact.save()
		self.assertEqual(self.members(), set())
		self.assertEqual(user.reload().as_dict(), before)
		contact.append("email_ids", {"email_id": user.email, "is_primary": 1})
		contact.save()
		self.assertEqual(self.members(), {user.name})
		self.assertEqual(user.reload().as_dict(), before)

	def test_contact_phone_edits_do_not_sync_account_mobile(self):
		user = self.make_user(mobile_no=self.phone())
		contact = self.make_contact(user)
		before = user.reload().as_dict()
		contact.append("phone_nos", {"phone": self.phone(), "is_primary_mobile_no": 1})
		contact.save()
		contact.phone_nos[0].phone = self.phone()
		contact.save()
		contact.set("phone_nos", [])
		contact.save()
		self.assertEqual(user.reload().as_dict(), before)
		self.assertEqual(self.members(), {user.name})

	def test_one_contact_can_grant_multiple_sources(self):
		user = self.make_user()
		other = self.make_source()
		contact = self.make_contact(user)
		contact.append("links", {"link_doctype": "UOM", "link_name": other.name})
		contact.save()
		for source in (self.source, other):
			self.assertEqual(self.members(source), {user.name})
		contact.remove(contact.links[0])
		contact.save()
		self.assertEqual(self.members(), set())
		self.assertEqual(self.members(other), {user.name})
		self.assertTrue(user.reload().enabled)

	def test_removing_one_contact_email_preserves_another_contact_grant(self):
		user = self.make_user()
		first = self.make_contact(user)
		second = self.make_contact(user)
		first.set("email_ids", [])
		first.save()
		self.assertEqual(self.members(), {user.name})
		second.set("email_ids", [])
		second.save()
		self.assertEqual(self.members(), set())
		self.assertTrue(user.reload().enabled)

	def test_user_created_after_contact_refreshes_existing_email_grants(self):
		email = self.email()
		# A secondary email avoids native User creation updating this Contact;
		# the User lifecycle hook must find the email membership itself.
		self.make_contact(emails=[self.email(), email])
		self.assertEqual(self.members(), set())
		user = self.make_user(email=email)
		self.assertEqual(self.members(), {user.name})

	def test_user_role_changes_refresh_all_sources(self):
		user = self.make_user(partner_role=False)
		other = self.make_source()
		for source in (self.source, other):
			self.make_contact(source=source, emails=[self.email(), user.email])
			self.assertEqual(self.members(source), set())
		user.append("roles", {"role": "YRP Partner"})
		user.save()
		for source in (self.source, other):
			self.assertEqual(self.members(source), {user.name})
		user.set("roles", [row for row in user.roles if row.role != "YRP Partner"])
		user.save()
		for source in (self.source, other):
			self.assertEqual(self.members(source), set())

	def test_user_enabled_changes_refresh_existing_grants(self):
		user = self.make_user()
		self.make_contact(emails=[self.email(), user.email])
		self.assertEqual(self.members(), {user.name})
		user.enabled = 0
		user.save()
		self.assertEqual(self.members(), set())
		self.source.save()
		self.partner_type.save()
		self.assertFalse(user.reload().enabled)
		user.enabled = 1
		user.save()
		self.assertEqual(self.members(), {user.name})

	def test_user_rename_hook_refreshes_old_and_new_email_grants(self):
		user = self.make_user()
		old_email, new_email = user.email, self.email()
		old_contact = self.make_contact(user, emails=[self.email(), old_email])
		other = self.make_source()
		self.make_contact(source=other, emails=[self.email(), new_email])
		self.assertEqual(self.members(), {user.name})
		self.assertEqual(self.members(other), set())
		def persist_renamed_email(doc, old_name, new_name, merge=False):
			frappe.db.set_value("User", new_name, "email", new_name)

		# Native User.after_rename scans every table's ownership history, even
		# for a synthetic user. Retain its persisted email update without that
		# unrelated scan; normal Link renaming and YRP's real hook still run.
		with patch("frappe.core.doctype.user.user.User.after_rename", new=persist_renamed_email):
			frappe.rename_doc("User", user.name, new_email, force=True, show_alert=False)
		self.assertIn(old_email, [row.email_id for row in old_contact.reload().email_ids])
		self.assertEqual(old_contact.user, new_email)
		self.assertEqual(self.members(), set())
		self.assertEqual(self.members(other), {new_email})

	def test_user_deletion_hook_revokes_membership_before_account_is_deleted(self):
		user, remaining = self.make_user(), self.make_user()
		self.make_contact(emails=[user.email, remaining.email])
		self.assertEqual(self.members(), {user.name, remaining.name})
		# Test access removal while the User still exists. Native User cleanup
		# scans unrelated document history, so skip only that controller method;
		# run_method still executes the actual registered YRP deletion hook.
		with patch("frappe.core.doctype.user.user.User.on_trash", new=lambda doc: None):
			user.run_method("on_trash")
		self.assertTrue(frappe.db.exists("User", user.name))
		self.assertEqual(self.members(), {remaining.name})

	def test_source_save_repairs_derived_membership_without_contact_changes(self):
		user = self.make_user()
		contact = self.make_contact(user)
		before = contact.reload().as_dict()
		frappe.db.delete("YRP Partner User", {"parent": self.partner().name})
		self.assertEqual(self.members(), set())
		self.source.save()
		self.assertEqual(self.members(), {user.name})
		self.assertEqual(contact.reload().as_dict(), before)
