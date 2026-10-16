import frappe
from frappe.model import get_permitted_fields
from frappe.utils import cint

EXCLUDED_FIELDTYPES = {
	"Attach",
	"Attach Image",
	"Button",
	"Code",
	"Column Break",
	"Fold",
	"Heading",
	"HTML",
	"HTML Editor",
	"Image",
	"JSON",
	"Password",
	"Section Break",
	"Signature",
	"Tab Break",
	"Table",
	"Table MultiSelect",
	"Text Editor",
}
STANDARD_FIELDS = (
	{"fieldname": "name", "fieldtype": "Data", "label": "ID", "options": ""},
	{"fieldname": "owner", "fieldtype": "Link", "label": "Created By", "options": "User"},
	{"fieldname": "creation", "fieldtype": "Datetime", "label": "Created On", "options": ""},
	{"fieldname": "modified_by", "fieldtype": "Link", "label": "Last Updated By", "options": "User"},
	{"fieldname": "modified", "fieldtype": "Datetime", "label": "Last Updated On", "options": ""},
)


def report_field_policy(doctype: str) -> list[dict]:
	"""Return readable value fields suitable for linked report columns."""

	frappe.has_permission(doctype, ptype="read", throw=True)
	meta = frappe.get_meta(doctype)
	permitted = set(get_permitted_fields(doctype, permission_type="read", ignore_virtual=True))
	readable_permlevels = set(meta.get_permlevel_access(parenttype=doctype))
	masked = {field.fieldname for field in meta.get_masked_fields()}
	fields = [dict(field) for field in STANDARD_FIELDS if field["fieldname"] in permitted]
	for field in meta.fields:
		if _is_allowed(field, permitted, masked, readable_permlevels):
			fields.append(_field_payload(field))
	if cint(meta.is_submittable) and "docstatus" in permitted:
		fields.append({"fieldname": "docstatus", "fieldtype": "Int", "label": "Document Status", "options": ""})
	return fields


def _is_allowed(field, permitted, masked, readable_permlevels) -> bool:
	return bool(
		field.fieldname
		and field.fieldname in permitted
		and field.fieldname not in masked
		and not cint(field.hidden)
		and not cint(field.is_virtual)
		and field.fieldtype not in EXCLUDED_FIELDTYPES
		and (cint(field.permlevel) == 0 or cint(field.permlevel) in readable_permlevels)
	)


def _field_payload(field) -> dict:
	return {
		"fieldname": field.fieldname,
		"fieldtype": field.fieldtype or "Data",
		"label": field.label or field.fieldname,
		"options": field.options or "",
		"precision": field.precision,
	}
