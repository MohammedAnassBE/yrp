import json
from collections.abc import Mapping

import frappe
from frappe import _

from yrp.web_reports.registry import filter_fieldtype, filter_options


def authorize_filters(schema, filters=None) -> dict:
	"""Validate report filters and enforce Link document access."""

	filters = _parse_filters(filters)
	unknown = sorted(set(filters) - set(schema))
	if unknown:
		frappe.throw(_("Unknown report filters: {0}").format(", ".join(unknown)))
	for fieldname, value in filters.items():
		specification = schema[fieldname]
		if filter_fieldtype(specification) == "MultiSelectList":
			if value in (None, "", []):
				continue
			members = value
			if isinstance(members, str) and members.lstrip().startswith("["):
				try:
					members = json.loads(members)
				except ValueError:
					pass  # A document name can start with a bracket.
			if not isinstance(members, list):
				members = [members]
			members = [member for member in members if member is not None and member != ""]
			for member in members:
				_validate_link_value(fieldname, filter_options(specification), member)
			filters[fieldname] = members
			continue
		if filter_fieldtype(specification) != "Link" or value in (None, ""):
			continue
		_validate_link_value(fieldname, filter_options(specification), value)
	return filters


def _parse_filters(filters) -> dict:
	if isinstance(filters, str):
		try:
			filters = frappe.parse_json(filters)
		except (TypeError, ValueError) as exception:
			raise frappe.ValidationError(_("Filters must be a valid JSON object.")) from exception
	if filters is None:
		return {}
	if not isinstance(filters, Mapping):
		frappe.throw(_("Filters must be an object."))
	return dict(filters)


def _validate_link_value(fieldname: str, doctype: str, value) -> None:
	if not isinstance(value, str):
		frappe.throw(_("Filter {0} must be a document name.").format(fieldname))
	if frappe.has_permission(doctype, "read", doc=value) or frappe.has_permission(
		doctype, "select", doc=value
	):
		return
	frappe.throw(
		_("You do not have permission to access {0}: {1}.").format(doctype, value),
		frappe.PermissionError,
	)
