from collections.abc import Mapping

import frappe
from frappe import _
from frappe.utils import cint

from yrp.web_reports.fields import report_field_policy
from yrp.web_reports.registry import get_web_report

LAYOUT_DOCTYPE = "YRP User Report Layout"
COLUMN_DOCTYPE = "YRP User Report Layout Column"
ALIGNS = {"auto", "left", "center", "right"}
MAX_LINKED_COLUMNS = 20


def get_report_layout(report_key: str) -> dict:
	entry = get_web_report(report_key)
	name = _layout_name(frappe.session.user, report_key)
	layout = None
	if name:
		layout = frappe.get_all(
			COLUMN_DOCTYPE,
			filters={"parent": name, "parenttype": LAYOUT_DOCTYPE},
			fields=["column_key", "kind", "source_column", "target_field", "visible", "align", "width"],
			order_by="idx asc",
		)
	return {"layout": layout, "link_sources": _link_source_payload(entry)}


def save_report_layout(report_key: str, columns=None) -> dict:
	entry = get_web_report(report_key)
	validated = _validate_columns(entry, _parse_columns(columns))
	user = frappe.session.user
	name = _layout_name(user, report_key)
	doc = frappe.get_doc(LAYOUT_DOCTYPE, name) if name else frappe.new_doc(LAYOUT_DOCTYPE)
	doc.user = user
	doc.report_key = report_key
	doc.set("columns", [])
	for column in validated:
		doc.append("columns", column)
	doc.save(ignore_permissions=True)
	return get_report_layout(report_key)


def reset_report_layout(report_key: str) -> dict:
	get_web_report(report_key)
	name = _layout_name(frappe.session.user, report_key)
	if name:
		frappe.delete_doc(LAYOUT_DOCTYPE, name, ignore_permissions=True, force=True)
	return get_report_layout(report_key)


def _validate_columns(entry, columns) -> list[dict]:
	validated = []
	seen = set()
	linked_count = 0
	allowed_fields = {}
	for column in columns:
		if not isinstance(column, Mapping):
			frappe.throw(_("Each report layout column must be an object."))
		kind = column.get("kind")
		column_key = column.get("column_key")
		if kind == "base":
			_validate_base_column(entry, column_key)
			source, target = "", ""
		elif kind == "linked":
			source, target = _validate_linked_column(entry, column, column_key, allowed_fields)
			linked_count += 1
		else:
			frappe.throw(_("Report layout column kind must be base or linked."))
		if column_key in seen:
			frappe.throw(_("Duplicate report layout column: {0}").format(column_key))
		seen.add(column_key)
		validated.append(_validated_column(column, column_key, kind, source, target))
	if linked_count > MAX_LINKED_COLUMNS:
		frappe.throw(_("A report layout may contain at most {0} linked columns.").format(MAX_LINKED_COLUMNS))
	return validated


def _validate_base_column(entry, column_key) -> None:
	fieldname = column_key.removeprefix("base:") if isinstance(column_key, str) else ""
	if column_key != f"base:{fieldname}" or fieldname not in entry.base_columns:
		frappe.throw(_("Unknown base report column: {0}").format(column_key))


def _validate_linked_column(entry, column, column_key, allowed_fields):
	source = column.get("source_column")
	target = column.get("target_field")
	if source not in entry.link_sources:
		frappe.throw(_("Unknown linked source column: {0}").format(source))
	if source not in allowed_fields:
		allowed_fields[source] = {
			field["fieldname"] for field in report_field_policy(entry.link_sources[source])
		}
	if target not in allowed_fields[source]:
		frappe.throw(_("Field {0} is not available for linked source {1}.").format(target, source))
	if column_key != f"linked:{source}:{target}":
		frappe.throw(_("Invalid linked report column key: {0}").format(column_key))
	return source, target


def _validated_column(column, column_key, kind, source, target) -> dict:
	align = column.get("align", "auto")
	width = column.get("width", 100)
	if align not in ALIGNS:
		frappe.throw(_("Report column alignment must be auto, left, center, or right."))
	if isinstance(width, bool) or not isinstance(width, (int, float)) or not 60 <= width <= 600:
		frappe.throw(_("Report column width must be between 60 and 600."))
	return {
		"column_key": column_key,
		"kind": kind,
		"source_column": source,
		"target_field": target,
		"visible": cint(column.get("visible", 1)),
		"align": align,
		"width": int(width),
	}


def _parse_columns(columns) -> list:
	if isinstance(columns, str):
		try:
			columns = frappe.parse_json(columns)
		except (TypeError, ValueError) as exception:
			raise frappe.ValidationError(_("Columns must be a valid JSON list.")) from exception
	if not isinstance(columns, (list, tuple)):
		frappe.throw(_("Columns must be a list."))
	return list(columns)


def _layout_name(user: str, report_key: str):
	return frappe.db.get_value(LAYOUT_DOCTYPE, {"user": user, "report_key": report_key}, "name")


def _link_source_payload(entry) -> dict:
	return {
		source: {
			"label": entry.base_columns.get(source) or source.replace("_", " ").title(),
			"doctype": doctype,
		}
		for source, doctype in entry.link_sources.items()
	}
