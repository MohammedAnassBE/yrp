from collections.abc import Mapping

import frappe
from frappe import _

from yrp.web_reports.access import check_report_access
from yrp.web_reports.fields import report_field_policy
from yrp.web_reports.registry import get_web_report

MAX_NAMES_PER_SOURCE = 5_000
QUERY_CHUNK_SIZE = 500


def get_link_field_options(report_key: str, source_column: str) -> list[dict]:
	entry = get_web_report(report_key)
	check_report_access(entry)
	doctype = _source_doctype(entry, source_column)
	return report_field_policy(doctype)


def get_linked_values(report_key: str, requests=None) -> dict:
	entry = get_web_report(report_key)
	check_report_access(entry)
	grouped = _group_requests(entry, _parse_requests(requests))
	return {
		source: _fetch_values(entry.link_sources[source], values["fields"], values["names"])
		for source, values in grouped.items()
	}


def _group_requests(entry, requests) -> dict:
	grouped = {}
	for request in requests:
		if not isinstance(request, Mapping):
			frappe.throw(_("Each linked-value request must be an object."))
		source = request.get("source_column")
		doctype = _source_doctype(entry, source)
		allowed = {field["fieldname"] for field in report_field_policy(doctype)}
		fields = _string_list(request.get("fields"), _("Fields"))
		names = _string_list(request.get("names"), _("Names"))
		invalid = sorted(set(fields) - allowed)
		if invalid:
			frappe.throw(_("Fields are not available for {0}: {1}").format(doctype, ", ".join(invalid)))
		bucket = grouped.setdefault(source, {"fields": set(), "names": set()})
		bucket["fields"].update(fields)
		bucket["names"].update(name for name in names if name)
		if len(bucket["names"]) > MAX_NAMES_PER_SOURCE:
			frappe.throw(_("A linked source may contain at most {0} names.").format(MAX_NAMES_PER_SOURCE))
	return grouped


def _fetch_values(doctype: str, fields: set[str], names: set[str]) -> dict:
	result = {}
	fieldnames = sorted(fields)
	ordered_names = sorted(names)
	for start in range(0, len(ordered_names), QUERY_CHUNK_SIZE):
		chunk = ordered_names[start : start + QUERY_CHUNK_SIZE]
		rows = frappe.get_list(
			doctype,
			filters={"name": ["in", chunk]},
			fields=["name", *[field for field in fieldnames if field != "name"]],
			limit_page_length=0,
		)
		for row in rows:
			result[row.name] = {field: row.get(field) for field in fieldnames}
	return result


def _source_doctype(entry, source_column: str) -> str:
	if source_column not in entry.link_sources:
		frappe.throw(_("Unknown linked source column: {0}").format(source_column))
	return entry.link_sources[source_column]


def _parse_requests(requests) -> list:
	if isinstance(requests, str):
		try:
			requests = frappe.parse_json(requests)
		except (TypeError, ValueError) as exception:
			raise frappe.ValidationError(_("Requests must be a valid JSON list.")) from exception
	if requests is None:
		return []
	if not isinstance(requests, (list, tuple)):
		frappe.throw(_("Requests must be a list."))
	return list(requests)


def _string_list(values, label: str) -> list[str]:
	if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) for value in values):
		frappe.throw(_("{0} must be a list of strings.").format(label))
	return list(values)
