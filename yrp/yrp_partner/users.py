"""Provision one login per Contact and retire only accounts owned by YRP.

Email is the preferred identity; mobile-only Contacts use the Partner Type format.
No passwords, OTP bypasses, or welcome messages are created by this module.
"""

import re
from string import Formatter

import frappe
from frappe import _
from frappe.utils import validate_email_address


_PROVISIONING_TOKEN = object()


class PartnerUserMixin:
	"""Treat trusted Contact provisioning as bulk creation, not public sign-up.

	Frappe exempts imports from its hourly User creation throttle. Limit that
	context to the native before_insert method: defaults, validation, Contact
	callbacks and notifications must otherwise retain their normal behavior.
	The private object cannot be supplied through a JSON/API request.
	"""

	def before_insert(self):
		if self.flags.yrp_provisioning_token is not _PROVISIONING_TOKEN:
			return super().before_insert()
		previous = frappe.flags.in_import
		try:
			frappe.flags.in_import = True
			return super().before_insert()
		finally:
			frappe.flags.in_import = previous


def mobile_key(value):
	"""Remove display punctuation without guessing a country code."""
	return re.sub(r"\D", "", value or "")


def validate_email_format(value):
	"""Accept only the documented hash/mobile placeholders and a valid email."""
	try:
		fields = [field for _, field, _, _ in Formatter().parse(value or "") if field]
		if not {"hash", "mobile_no"}.issubset(fields) or set(fields) - {"hash", "mobile_no"}:
			raise ValueError
		validate_email_address(value.format(hash="abc123", mobile_no="15550001234"), throw=True)
	except (ValueError, KeyError, IndexError):
		frappe.throw(_("Email format must contain {hash} and {mobile_no}, for example {hash}+{mobile_no}@yrp.com."))


def contact_settings(doc):
	"""Read settings for the source records linked through standard Dynamic Links."""
	doctypes = list({row.link_doctype for row in doc.links if row.link_doctype})
	if not doctypes:
		return []
	return frappe.get_all("YRP Partner Type", filters={"reference_doctype": ["in", doctypes]},
		fields=["name", "generate_email_when_missing", "generated_email_format"], order_by="name")


def prepare_contact_user(doc, method=None):
	"""Before Contact validation, resolve/create its login and populate missing email."""
	if not frappe.db.table_exists("YRP Partner Type"):
		return
	settings = contact_settings(doc)
	if not settings:
		return
	previous = doc.get_doc_before_save()
	if doc.is_new() or (previous and any(
		doc.get(field) != previous.get(field) for field in ("user", "email_ids", "phone_nos", "links")
	)):
		configured = set(frappe.get_all("YRP Partner Type", pluck="reference_doctype"))
		for link in doc.links:
			if link.link_doctype in configured:
				frappe.get_doc(link.link_doctype, link.link_name).check_permission("write")
	previous_user = frappe.db.get_value("Contact", doc.name, "user") if not doc.is_new() else None
	old = frappe.get_doc("User", previous_user) if previous_user else None
	emails = [row.email_id.strip().lower() for row in doc.email_ids if row.email_id]
	email = next((row.email_id.strip().lower() for row in doc.email_ids if row.is_primary and row.email_id), None)
	email = email or next(iter(emails), None)
	mobile = next((row.phone for row in doc.phone_nos if row.is_primary_mobile_no and row.phone), None)
	mobile = mobile_key(mobile or next((row.phone for row in doc.phone_nos if row.phone), None))
	if old and old.yrp_partner_generated_email and old.name in emails and mobile != mobile_key(old.mobile_no):
		doc.set("email_ids", [row for row in doc.email_ids if row.email_id.strip().lower() != old.name])
		email = next((row.email_id.strip().lower() for row in doc.email_ids if row.email_id), None)
	if not email and not mobile:
		if old and old.yrp_partner_managed:
			doc.user = None
		return
	by_email = frappe.db.get_value("User", {"email": email}, "name") if email else None
	by_mobile = frappe.get_all("User", filters={"mobile_no": mobile}, pluck="name") if mobile else []
	if len(by_mobile) > 1 or (by_email and by_mobile and by_email != by_mobile[0]):
		frappe.throw(_("Email and mobile belong to different users. Resolve the Contact details first."))
	name = by_email or next(iter(by_mobile), None)
	if name in ("Administrator", "Guest"):
		frappe.throw(_("Standard system users cannot be partner logins."))
	if name and email and name != email:
		frappe.throw(_("This mobile is linked to another login email. Resolve the existing account before changing it."))
	generated = False
	if not email:
		formats = {row.generated_email_format for row in settings if row.generate_email_when_missing}
		if not formats:
			return
		if len(formats) != 1:
			frappe.throw(_("Linked Partner Types must use the same generated email format."))
		if name:
			email = frappe.db.get_value("User", name, "email")
		else:
			pattern = formats.pop()
			validate_email_format(pattern)
			email = pattern.format(hash=frappe.generate_hash(length=12), mobile_no=mobile).lower()
			generated = True
		doc.append("email_ids", {"email_id": email, "is_primary": 1})
	if name:
		user = frappe.get_doc("User", name)
		if not user.enabled and not user.yrp_partner_auto_disabled:
			frappe.throw(_("This user was disabled manually. A System Manager must enable it."))
		changed = False
		if user.yrp_partner_managed and user.mobile_no and not mobile:
			shared_mobile = any(
				row.parent != doc.name and mobile_key(row.phone) == mobile_key(user.mobile_no)
				for row in frappe.get_all("Contact Phone", filters={"parenttype": "Contact"}, fields=["parent", "phone"])
			)
			if not shared_mobile:
				user.mobile_no = None
				changed = True
		if mobile and not user.mobile_no:
			user.mobile_no = mobile
			changed = True
		if user.yrp_partner_auto_disabled:
			user.enabled = 1
			user.yrp_partner_auto_disabled = 0
			changed = True
		if "YRP Partner" not in [row.role for row in user.roles]:
			user.append("roles", {"role": "YRP Partner"})
			changed = True
		if changed:
			user.save(ignore_permissions=True)
	else:
		user = frappe.get_doc({
			"doctype": "User", "email": email, "first_name": doc.first_name or "Partner",
			"mobile_no": mobile or None, "enabled": 1, "send_welcome_email": 0,
			"yrp_partner_managed": 1, "yrp_partner_generated_email": int(generated),
			"roles": [{"role": "YRP Partner"}],
		})
		user.flags.yrp_provisioning_token = _PROVISIONING_TOKEN
		try:
			user.insert(ignore_permissions=True)
		finally:
			user.flags.pop("yrp_provisioning_token", None)
	doc.user = user.name


def disable_unused_user(name, exclude_contact=None):
	"""Disable a YRP-created account only after checking all other Contact identities."""
	if not name or name in ("Administrator", "Guest"):
		return
	user = frappe.get_doc("User", name)
	if not user.yrp_partner_managed or not user.enabled:
		return
	filters = {"user": name}
	if exclude_contact:
		filters["name"] = ["!=", exclude_contact]
	if frappe.db.exists("Contact", filters):
		return
	for doctype, field, value in (("Contact Email", "email_id", user.email), ("Contact Phone", "phone", user.mobile_no)):
		if not value:
			continue
		for row in frappe.get_all(doctype, filters={"parenttype": "Contact"}, fields=["parent", field]):
			if row.parent == exclude_contact:
				continue
			match = mobile_key(row.get(field)) == mobile_key(value) if field == "phone" else (row.get(field) or "").lower() == value.lower()
			if match:
				return
	# Do not run User.on_update here: its queued Contact synchronization would
	# recreate the identity that was just removed from the Contact.
	user.db_set({"enabled": 0, "yrp_partner_auto_disabled": 1})
	from frappe.sessions import clear_sessions
	from frappe.desk.doctype.notification_settings.notification_settings import toggle_notifications

	clear_sessions(user=name, force=True)
	toggle_notifications(name, enable=False, ignore_permissions=True)
	frappe.clear_cache(user=name)
