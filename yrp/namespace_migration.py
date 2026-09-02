"""Data helpers for the YRP/SD YRP DocType namespace migration."""

from __future__ import annotations

import json
from pathlib import Path

import frappe


PREFIX_BY_MODULE = {
	"YRP": "YRP ",
	"YRP Stock": "YRP ",
	"Essdee YRP": "SD YRP ",
}


def rewrite_owned_doctype_discriminators(app_names: tuple[str, ...]) -> None:
	"""Rewrite stored DocType selectors that ``rename_doc`` cannot infer.

	Frappe updates Link/Table metadata while renaming a DocType, but application
	fields such as ``against``, ``ref_doctype`` and ``voucher_type`` may be
	Select/Data fields controlling a Dynamic Link. Restrict updates to standard
	DocTypes owned by the supplied custom apps so an ERPNext discriminator is
	never rewritten merely because its value is also named ``Item`` or
	``Supplier``.
	"""

	records = list(_iter_namespaced_doctypes(app_names))
	renames = {
		name.removeprefix(prefix): name
		for name, prefix, _fields in records
	}
	final_names = set(renames.values())

	for doctype, _prefix, fields in records:
		selector_fields = {
			field.get("options")
			for field in fields
			if field.get("fieldtype") == "Dynamic Link" and field.get("options")
		}
		selector_fields.update(
			field.get("fieldname")
			for field in fields
			if field.get("fieldtype") == "Select"
			and any(option in final_names for option in str(field.get("options") or "").splitlines())
		)

		if not selector_fields or not frappe.db.exists("DocType", doctype):
			continue
		is_single = bool(frappe.db.get_value("DocType", doctype, "issingle"))
		for fieldname in sorted(selector_fields):
			if not fieldname:
				continue
			if is_single:
				current = frappe.db.get_single_value(doctype, fieldname)
				if current in renames:
					frappe.db.set_single_value(doctype, fieldname, renames[current])
			elif frappe.db.has_column(doctype, fieldname):
				for old_name, new_name in renames.items():
					frappe.db.set_value(
						doctype,
						{fieldname: old_name},
						fieldname,
						new_name,
						update_modified=False,
					)


def rename_owned_customization_records(app_names: tuple[str, ...]) -> None:
	"""Align customization record names with their namespaced target DocType.

	``rename_doc("DocType", ...)`` updates ``Custom Field.dt`` and
	``Property Setter.doc_type``, but their own globally unique names retain the
	old DocType prefix. Those stale names can collide with ERPNext or India
	Compliance fixtures on a combined site.
	"""

	doctypes = [name for name, _prefix, _fields in _iter_namespaced_doctypes(app_names)]
	if not doctypes:
		return

	for row in frappe.get_all(
		"Custom Field",
		filters={"dt": ["in", doctypes]},
		fields=["name", "dt", "fieldname"],
	):
		desired_name = f"{row.dt}-{row.fieldname}"
		_rename_customization_record("Custom Field", row.name, desired_name)

	for row in frappe.get_all(
		"Property Setter",
		filters={"doc_type": ["in", doctypes]},
		fields=["name", "doc_type", "field_name", "row_name", "property"],
	):
		field = row.field_name or row.row_name or "main"
		desired_name = f"{row.doc_type}-{field}-{row.property}"
		_rename_customization_record("Property Setter", row.name, desired_name)


def drop_empty_legacy_namespace_tables(app_names: tuple[str, ...]) -> list[str]:
	"""Drop only unregistered, empty tables superseded by namespaced DocTypes.

	A combined ERPNext site may legitimately own the legacy identity (for example
	``Supplier`` or ``Purchase Invoice``); those tables are never candidates. A
	leftover custom table is removed only when the namespaced DocType is installed
	and the old physical table contains no rows. Any non-empty residue fails the
	patch closed so historical data cannot be discarded silently.
	"""

	dropped: list[str] = []
	seen: set[str] = set()
	for target_name, prefix, _fields in _iter_namespaced_doctypes(app_names):
		legacy_name = target_name.removeprefix(prefix)
		if legacy_name in seen:
			continue
		seen.add(legacy_name)
		if frappe.db.exists("DocType", legacy_name):
			continue
		if not frappe.db.exists("DocType", target_name):
			frappe.throw(
				f"Cannot clean legacy table {legacy_name}: target DocType {target_name} is missing"
			)
		if not frappe.db.table_exists(legacy_name, cached=False):
			continue

		legacy_table = "tab" + legacy_name
		quoted_table = "`" + legacy_table.replace("`", "``") + "`"
		row_count = int(frappe.db.sql(f"SELECT COUNT(*) FROM {quoted_table}")[0][0])
		if row_count:
			frappe.throw(
				f"Refusing to drop non-empty legacy namespace table {legacy_table} "
				f"({row_count} rows); migrate or review it explicitly"
			)
		frappe.db.sql_ddl(f"DROP TABLE {quoted_table}")
		dropped.append(legacy_name)
	return dropped


def _rename_customization_record(record_type: str, old_name: str, new_name: str) -> None:
	if old_name == new_name:
		return
	if frappe.db.exists(record_type, new_name):
		frappe.throw(
			f"Both {record_type} {old_name} and {new_name} exist during the namespace migration"
		)
	frappe.rename_doc(
		record_type,
		old_name,
		new_name,
		force=True,
		show_alert=False,
		rebuild_search=False,
	)


def _iter_namespaced_doctypes(app_names: tuple[str, ...]):
	installed_apps = set(frappe.get_installed_apps())
	for app_name in app_names:
		if app_name not in installed_apps:
			continue
		app_path = Path(frappe.get_app_path(app_name))
		for path in sorted(app_path.rglob("*.json")):
			try:
				data = json.loads(path.read_text())
			except (OSError, UnicodeDecodeError, json.JSONDecodeError):
				continue
			if not isinstance(data, dict) or data.get("doctype") != "DocType":
				continue
			prefix = PREFIX_BY_MODULE.get(data.get("module"))
			name = data.get("name")
			if prefix and isinstance(name, str) and name.startswith(prefix):
				yield name, prefix, data.get("fields") or []
