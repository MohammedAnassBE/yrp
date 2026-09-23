import frappe

from yrp.yrp_partner.doctype.yrp_partner.test_yrp_partner import TestYRPPartner


class TestPartnerUsers(TestYRPPartner):
	def setUp(self):
		super().setUp()
		self.partner_type.generate_email_when_missing = 1
		self.partner_type.generated_email_format = "{hash}+{mobile_no}@yrp.com"
		self.partner_type.save()

	def identity_contact(self, email=None, phone=None):
		return frappe.get_doc({
			"doctype": "Contact", "first_name": "Test Identity " + frappe.generate_hash(length=8),
			"email_ids": [{"email_id": email, "is_primary": 1}] if email else [],
			"phone_nos": [{"phone": phone, "is_primary_mobile_no": 1}] if phone else [],
			"links": [{"link_doctype": "UOM", "link_name": self.source.name}],
		}).insert()

	def phone(self):
		import secrets
		return "1555" + str(secrets.randbelow(10_000_000)).zfill(7)

	def email(self):
		return "test-" + frappe.generate_hash(length=12) + "@example.invalid"

	def test_email_and_mobile_create_one_user(self):
		email, phone = self.email(), self.phone()
		contact = self.identity_contact(email, phone)
		user = frappe.get_doc("User", contact.user)
		self.assertEqual(user.name, email)
		self.assertEqual(user.mobile_no, phone)
		self.assertTrue(user.enabled)
		self.assertIn("YRP Partner", [row.role for row in user.roles])
		self.assertEqual(frappe.db.count("User", {"mobile_no": phone}), 1)

	def test_mobile_only_generates_and_saves_contact_email(self):
		phone = self.phone()
		contact = self.identity_contact(phone=phone)
		self.assertRegex(contact.user, r"^[a-z0-9]{12}\+" + phone + r"@yrp\.com$")
		self.assertEqual(contact.email_ids[0].email_id, contact.user)
		contact.save()
		self.assertEqual(frappe.db.count("User", {"mobile_no": phone}), 1)

	def test_checkbox_off_does_not_generate_email(self):
		self.partner_type.generate_email_when_missing = 0
		self.partner_type.save()
		contact = self.identity_contact(phone=self.phone())
		self.assertFalse(contact.user)
		self.assertFalse(contact.email_ids)

	def test_removing_last_email_disables_managed_user(self):
		contact = self.identity_contact(email=self.email())
		user = contact.user
		contact.set("email_ids", [])
		contact.save()
		self.assertFalse(contact.user)
		self.assertFalse(frappe.db.get_value("User", user, "enabled"))

	def test_shared_contact_keeps_user_enabled(self):
		email = self.email()
		first = self.identity_contact(email=email)
		second = self.identity_contact(email=email)
		self.assertEqual(first.user, second.user)
		first.set("email_ids", [])
		first.save()
		self.assertTrue(frappe.db.get_value("User", second.user, "enabled"))

	def test_removed_mobile_removes_synthetic_email_and_disables(self):
		contact = self.identity_contact(phone=self.phone())
		user = contact.user
		contact.set("phone_nos", [])
		contact.save()
		self.assertFalse(contact.email_ids)
		self.assertFalse(contact.user)
		self.assertFalse(frappe.db.get_value("User", user, "enabled"))

	def test_automatically_disabled_user_is_reused(self):
		email = self.email()
		contact = self.identity_contact(email=email)
		user = contact.user
		contact.set("email_ids", [])
		contact.save()
		contact.append("email_ids", {"email_id": email, "is_primary": 1})
		contact.save()
		self.assertEqual(contact.user, user)
		self.assertTrue(frappe.db.get_value("User", user, "enabled"))

	def test_existing_unmanaged_user_is_not_disabled(self):
		user = self.make_user()
		contact = self.identity_contact(email=user.name)
		contact.set("email_ids", [])
		contact.user = None
		contact.save()
		self.assertTrue(frappe.db.get_value("User", user.name, "enabled"))

	def test_invalid_email_format_is_rejected(self):
		self.partner_type.generated_email_format = "{unknown}@example.invalid"
		with self.assertRaises(frappe.ValidationError):
			self.partner_type.save()

	def test_removing_mobile_keeps_email_login_without_stale_phone(self):
		contact = self.identity_contact(email=self.email(), phone=self.phone())
		user = contact.user
		contact.set("phone_nos", [])
		contact.save()
		self.assertTrue(frappe.db.get_value("User", user, "enabled"))
		self.assertFalse(frappe.db.get_value("User", user, "mobile_no"))

	def test_partner_cannot_link_contact_to_unauthorized_source(self):
		user = self.make_user()
		user.append("roles", {"role": "YRP Partner"})
		user.save()
		original = frappe.session.user
		try:
			frappe.set_user(user.name)
			with self.assertRaises(frappe.PermissionError):
				self.identity_contact(email=user.name)
		finally:
			frappe.set_user(original)
