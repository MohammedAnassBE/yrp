"""Synchronize derived Partner records from source records and linked Contacts."""

import frappe

_GENERATION_TOKEN = object()


def contact_users(reference_doctype, reference_name, exclude_contact=None, exclude_user=None):
	"""Match Contact emails to existing, enabled users with the Partner role.

	Contact.user and phone numbers are not identity grants. Multiple Contacts or
	emails may grant the same user access; removing one must preserve the others.
	"""
	return frappe.db.sql("""
		SELECT DISTINCT u.name
		FROM `tabDynamic Link` l
		JOIN `tabContact` c ON c.name=l.parent
		JOIN `tabContact Email` e ON e.parent=c.name
			AND e.parenttype='Contact' AND e.parentfield='email_ids'
		JOIN `tabUser` u ON u.email=e.email_id AND u.enabled=1
		JOIN `tabHas Role` r ON r.parent=u.name
			AND r.parenttype='User' AND r.parentfield='roles' AND r.role='YRP Partner'
		WHERE l.parenttype='Contact' AND l.parentfield='links'
			AND l.link_doctype=%s AND l.link_name=%s
			AND c.name != %s AND u.name != %s AND u.name NOT IN ('Administrator', 'Guest')
		ORDER BY u.name
	""", (reference_doctype, reference_name, exclude_contact or "", exclude_user or ""), pluck=True)


def ensure_partner(partner_type, reference_doctype, reference_name, exclude_contact=None, exclude_user=None):
	"""Reconcile a derived Partner without saving any Contact or User."""
	filters = {"partner_type": partner_type, "reference_name": reference_name}
	name = frappe.db.get_value("YRP Partner", filters, "name")
	partner = frappe.get_doc("YRP Partner", name, for_update=True) if name else None
	users = contact_users(reference_doctype, reference_name, exclude_contact, exclude_user)
	if name:
		if sorted(row.user for row in partner.users) == users:
			return
		partner.set("users", [{"user": user} for user in users])
		partner.flags.generation_token = _GENERATION_TOKEN
		partner.save(ignore_permissions=True)
		return
	partner = frappe.get_doc({
		"doctype": "YRP Partner", **filters, "reference_doctype": reference_doctype,
		"users": [{"user": user} for user in users],
	})
	# Synchronization grants no roles; membership comes only from linked Contacts.
	partner.flags.generation_token = _GENERATION_TOKEN
	try:
		partner.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		if not frappe.db.exists("YRP Partner", filters):
			raise


def sync_document(doc, method=None):
	"""Handle source saves and Contact updates through standard document hooks."""
	if doc.doctype.startswith("YRP Partner") or not frappe.db.table_exists("YRP Partner Type"):
		return
	for partner_type in frappe.get_all(
		"YRP Partner Type", filters={"reference_doctype": doc.doctype}, pluck="name"
	):
		ensure_partner(partner_type, doc.doctype, doc.name)
	if doc.doctype == "Contact":
		sync_contact(doc, method)


def sync_contact(doc, method=None):
	"""Refresh old and new Contact links without changing login accounts."""
	if not frappe.db.table_exists("YRP Partner Type"):
		return
	references = {(row.link_doctype, row.link_name) for row in doc.links}
	previous = doc.get_doc_before_save()
	if previous:
		references.update((row.link_doctype, row.link_name) for row in previous.links)
	for doctype, name in sorted(references):
		if not frappe.db.exists(doctype, name):
			continue
		for partner_type in frappe.get_all(
			"YRP Partner Type", filters={"reference_doctype": doctype}, pluck="name"
		):
			ensure_partner(partner_type, doctype, name, doc.name if method == "on_trash" else None)


def validate_contact_links(doc, method=None):
	"""Changing an access-bearing Contact requires authority over its sources."""
	if not frappe.db.table_exists("YRP Partner Type"):
		return
	previous = doc.get_doc_before_save()
	def access_values(contact):
		return (
			{row.email_id for row in contact.email_ids},
			{(row.link_doctype, row.link_name) for row in contact.links},
		)
	if not doc.is_new() and previous and access_values(doc) == access_values(previous):
		return
	configured = set(frappe.get_all("YRP Partner Type", pluck="reference_doctype"))
	references = {(row.link_doctype, row.link_name) for row in doc.links}
	if previous:
		references.update((row.link_doctype, row.link_name) for row in previous.links)
	for doctype, name in sorted(references):
		if doctype in configured and frappe.db.exists(doctype, name):
			frappe.get_doc(doctype, name).check_permission("write")


def sync_user_memberships(doc, method=None, *args, **kwargs):
	"""Refresh derived access after normal User administration; never edit users.

	Existing memberships cover a changed/removed email; email matches cover new
	users and newly assigned roles. Only affected business records are visited.
	"""
	if not frappe.db.table_exists("YRP Partner Type"):
		return
	previous = doc.get_doc_before_save()
	# Native User.after_rename updates email in SQL; the hook document can still
	# contain its old email, while linked membership rows already use the new name.
	current_email = frappe.db.get_value("User", doc.name, "email")
	emails = {value for value in (current_email, doc.email, previous.email if previous else None) if value}
	contacts = frappe.get_all("Contact Email", filters={
		"parenttype": "Contact", "parentfield": "email_ids", "email_id": ["in", sorted(emails)],
	}, pluck="parent") if emails else []
	references = set()
	if contacts:
		references.update(tuple(row) for row in frappe.get_all("Dynamic Link", filters={
			"parenttype": "Contact", "parentfield": "links", "parent": ["in", contacts],
		}, fields=["link_doctype", "link_name"], as_list=True))
	partner_names = frappe.get_all("YRP Partner User", filters={
		"parenttype": "YRP Partner", "parentfield": "users", "user": doc.name,
	}, pluck="parent")
	if partner_names:
		references.update(tuple(row) for row in frappe.get_all("YRP Partner", filters={
			"name": ["in", partner_names],
		}, fields=["reference_doctype", "reference_name"], as_list=True))
	for doctype, name in sorted(references):
		if not frappe.db.exists(doctype, name):
			continue
		for partner_type in frappe.get_all("YRP Partner Type", filters={
			"reference_doctype": doctype,
		}, pluck="name"):
			ensure_partner(partner_type, doctype, name, exclude_user=doc.name if method == "on_trash" else None)
