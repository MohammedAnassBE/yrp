"""Synchronize derived Partner records from source records and linked Contacts."""

import frappe

_GENERATION_TOKEN = object()


def contact_users(reference_doctype, reference_name, exclude_contact=None):
	"""Collect distinct linked Contact users; omit a Contact being deleted."""
	contacts = frappe.get_all("Dynamic Link", filters={
		"parenttype": "Contact", "parentfield": "links",
		"link_doctype": reference_doctype, "link_name": reference_name,
	}, pluck="parent")
	contacts = [name for name in contacts if name != exclude_contact]
	if not contacts:
		return []
	return sorted(set(filter(None, frappe.get_all(
		"Contact", filters={"name": ["in", contacts]}, pluck="user"
	))))


def ensure_partner(partner_type, reference_doctype, reference_name, exclude_contact=None):
	"""Provision linked identities and reconcile exactly one derived partner."""
	from yrp.yrp_partner.users import prepare_contact_user

	contacts = frappe.get_all("Dynamic Link", filters={
		"parenttype": "Contact", "parentfield": "links",
		"link_doctype": reference_doctype, "link_name": reference_name,
	}, pluck="parent")
	for contact_name in sorted(set(contacts) - {exclude_contact}):
		# User.after_insert can save this same Contact through ERPNext's native
		# hook. Lock before provisioning so only those nested, same-transaction
		# changes can intervene, then reconcile against their persisted result.
		contact = frappe.get_doc("Contact", contact_name, for_update=True)
		before = contact.as_dict()
		prepare_contact_user(contact)
		if frappe.db.get_value("Contact", contact_name, "modified") != before.modified:
			contact = frappe.get_doc("Contact", contact_name, for_update=True)
			before = contact.as_dict()
			prepare_contact_user(contact)
		if contact.as_dict() != before:
			contact.save(ignore_permissions=True)
	filters = {"partner_type": partner_type, "reference_name": reference_name}
	name = frappe.db.get_value("YRP Partner", filters, "name")
	users = contact_users(reference_doctype, reference_name, exclude_contact)
	if name:
		partner = frappe.get_doc("YRP Partner", name)
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
	"""Refresh both old and new links, then retire an unused previous login."""
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

	from yrp.yrp_partner.users import disable_unused_user

	old_user = previous.user if previous else None
	if method == "on_trash":
		disable_unused_user(doc.user, exclude_contact=doc.name)
	elif old_user and old_user != doc.user:
		disable_unused_user(old_user)
