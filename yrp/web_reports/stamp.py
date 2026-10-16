import hashlib

import frappe


def auth_stamp(user: str, entry, stamp_doctypes) -> str:
	"""Hash current permission inputs without using cached documents."""

	doctypes = tuple(sorted(set(stamp_doctypes or ())))
	payload = {
		"report_key": entry.key,
		"doctypes": doctypes,
		"roles": _simple_rows("Has Role", "parent = %s AND parenttype = 'User'", (user,)),
		"user_permissions": _simple_rows("User Permission", "user = %s", (user,)),
		"shares": _share_rows(user, doctypes),
		"docperms": _doctype_rows("DocPerm", "parent", doctypes),
		"custom_docperms": _doctype_rows("Custom DocPerm", "parent", doctypes),
		"metadata": _metadata_rows(doctypes),
	}
	if entry.kind == "frappe_report":
		payload["report"] = _report_rows(entry.report)
	serialized = frappe.as_json(payload, indent=None, separators=(",", ":"))
	return hashlib.sha1(serialized.encode(), usedforsecurity=False).hexdigest()


def _simple_rows(table: str, condition: str, values=()) -> list:
	return frappe.db.sql(
		f"SELECT name, modified FROM `tab{table}` WHERE {condition} ORDER BY name",
		values,
		as_list=True,
	)


def _share_rows(user: str, doctypes: tuple[str, ...]) -> list:
	if not doctypes:
		return []
	return frappe.db.sql(
		"""SELECT name, modified FROM `tabDocShare`
		WHERE (user = %s OR everyone = 1) AND share_doctype IN %s ORDER BY name""",
		(user, doctypes),
		as_list=True,
	)


def _doctype_rows(table: str, fieldname: str, doctypes: tuple[str, ...]) -> list:
	if not doctypes:
		return []
	return frappe.db.sql(
		f"SELECT name, modified FROM `tab{table}` WHERE `{fieldname}` IN %s ORDER BY name",
		(doctypes,),
		as_list=True,
	)


def _metadata_rows(doctypes: tuple[str, ...]) -> dict:
	return {
		"doctypes": _doctype_rows("DocType", "name", doctypes),
		"property_setters": _doctype_rows("Property Setter", "doc_type", doctypes),
		"custom_fields": _doctype_rows("Custom Field", "dt", doctypes),
	}


def _report_rows(report_name: str) -> dict:
	report = frappe.db.sql(
		"SELECT name, disabled, ref_doctype FROM `tabReport` WHERE name = %s",
		report_name,
		as_dict=True,
	)
	custom_roles = frappe.db.sql(
		"SELECT name, modified FROM `tabCustom Role` WHERE report = %s ORDER BY name",
		report_name,
		as_list=True,
	)
	parents = tuple(row[0] for row in custom_roles)
	return {
		"values": report,
		"roles": _simple_rows("Has Role", "parent = %s AND parenttype = 'Report'", (report_name,)),
		"custom_roles": custom_roles,
		"custom_role_rows": _custom_role_rows(parents),
	}


def _custom_role_rows(parents: tuple[str, ...]) -> list:
	if not parents:
		return []
	return frappe.db.sql(
		"SELECT name, modified FROM `tabHas Role` WHERE parenttype = 'Custom Role' AND parent IN %s ORDER BY name",
		(parents,),
		as_list=True,
	)
