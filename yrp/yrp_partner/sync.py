"""Generate one YRP Partner per configured source record.

Membership (the Partner's `users` table) is explicit. An administrator or a
custom app maintains it; yrp only reads it for permissions and never derives it.
"""

import frappe

_GENERATION_TOKEN = object()


def is_source_active(doctype, name):
	"""Read standard business offboarding flags without app-specific identities."""
	if not frappe.db.exists(doctype, name):
		return False
	meta = frappe.get_meta(doctype)
	fields = [field for field in ("disabled", "enabled", "status", "docstatus") if meta.has_field(field)]
	values = frappe.db.get_value(doctype, name, fields, as_dict=True) if fields else {}
	return not (
		values.get("disabled")
		or values.get("enabled") == 0
		or values.get("status") == "Left"
		or values.get("docstatus") == 2
	)


def ensure_source_membership(doctype, name, user):
	"""Bind an existing login to an active source; never create or enable Users.

	The caller owns identity resolution. This internal repair uses the same Partner
	creation and membership controller as ordinary document saves.
	"""
	from yrp.yrp_partner.member_roles import sync_member_roles

	frappe.only_for("System Manager")
	if not is_source_active(doctype, name):
		raise frappe.ValidationError(f"Inactive partner source: {doctype} {name}")
	frappe.get_doc("User", user)
	if user in {"Administrator", "Guest"}:
		raise frappe.ValidationError(f"Cannot repair protected identity membership: {user}")
	types = frappe.get_all("YRP Partner Type", {"reference_doctype": doctype}, pluck="name")
	if not types:
		raise frappe.ValidationError(f"No Partner Type for {doctype}")
	for partner_type in types:
		ensure_partner(partner_type, doctype, name)
		partner = frappe.get_doc(
			"YRP Partner", {"partner_type": partner_type, "reference_name": name}, for_update=True
		)
		if partner.disabled:
			raise frappe.ValidationError(f"Partner is disabled: {partner.name}")
		if user not in {row.user for row in partner.users}:
			partner.append("users", {"user": user})
			partner.save(ignore_permissions=True)
	sync_member_roles({user}, set())


def ensure_partner(partner_type, reference_doctype, reference_name, *, name=None):
	"""Create the source's Partner when it is missing; existing members are never changed."""
	filters = {"partner_type": partner_type, "reference_name": reference_name}
	if existing := frappe.db.exists("YRP Partner", filters):
		if name and existing != name:
			raise frappe.ValidationError(f"Partner {existing} conflicts with required historical name {name}")
		return
	if frappe.flags.defer_partner_generation and not name:
		raise frappe.ValidationError("Migration requires an explicit historical Partner name")
	partner = frappe.get_doc({"doctype": "YRP Partner", **filters, "reference_doctype": reference_doctype})
	partner.flags.generation_token = _GENERATION_TOKEN
	try:
		partner.insert(ignore_permissions=True, set_name=name)
	except frappe.UniqueValidationError:
		existing = frappe.db.exists("YRP Partner", filters)
		if not existing or (name and existing != name):
			raise


def sync_document(doc, method=None):
	"""Create missing Partners when a configured source record is saved."""
	if frappe.flags.defer_partner_generation:
		return
	if doc.doctype.startswith("YRP Partner") or not frappe.db.table_exists("YRP Partner Type"):
		return
	for partner_type in frappe.get_all(
		"YRP Partner Type", filters={"reference_doctype": doc.doctype}, pluck="name"
	):
		ensure_partner(partner_type, doc.doctype, doc.name)


def ensure_historical_partner(name, partner_type, reference_doctype):
	"""Preserve an unbound historical identity without inventing a source record.

	This internal import path retains an explicit name, validates the configured
	type and never overwrites an existing identity or grants membership roles.
	Unbound references use NULL so the business-binding unique index stays valid.
	"""
	if not name or not partner_type:
		raise frappe.ValidationError("Historical Partner requires a name and type")
	if not frappe.db.exists("YRP Partner Type", partner_type):
		raise frappe.ValidationError("Historical Partner requires an existing Partner Type")
	reference_doctype = reference_doctype or None
	if (
		frappe.db.get_value("YRP Partner Type", partner_type, "reference_doctype") or None
	) != reference_doctype:
		raise frappe.ValidationError("Historical Partner type does not match its reference DocType")
	if existing := frappe.db.get_value(
		"YRP Partner", name, ["partner_type", "reference_doctype", "reference_name"], as_dict=True
	):
		if (existing.partner_type, existing.reference_doctype or None) != (
			partner_type,
			reference_doctype,
		) or existing.reference_name:
			raise frappe.ValidationError(
				f"Historical Partner name conflicts with an existing identity: {name}"
			)
		return
	partner = frappe.get_doc(
		{
			"doctype": "YRP Partner",
			"partner_type": partner_type,
			"reference_doctype": reference_doctype,
			"reference_name": None,
		}
	)
	partner.flags.generation_token = _GENERATION_TOKEN
	partner.insert(ignore_permissions=True, ignore_mandatory=True, set_name=name)
