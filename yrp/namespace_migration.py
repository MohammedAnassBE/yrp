"""Data helpers for the YRP/SD YRP DocType namespace migration."""

from __future__ import annotations

import json
from collections import Counter
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


def reconcile_legacy_single_child_parents(app_names: tuple[str, ...]) -> dict[str, int]:
	"""Move or deduplicate child rows left under a renamed Single identity.

	Frappe renames the child ``parenttype`` when a Single DocType is renamed, but
	the child's ``parent`` can retain the old Single name. If model sync or setup
	later seeds the target identity too, an unscoped child query sees both copies.
	Exact duplicates are removed; every old-only row is moved to the target Single
	and appended after its existing rows so configuration data is never discarded.
	"""

	result = {"moved": 0, "deduplicated": 0}
	for target_name, prefix, fields in _iter_namespaced_doctypes(app_names):
		if not frappe.db.exists("DocType", target_name):
			continue
		if not frappe.db.get_value("DocType", target_name, "issingle"):
			continue
		legacy_name = target_name.removeprefix(prefix)
		for field in fields:
			if field.get("fieldtype") not in {"Table", "Table MultiSelect"}:
				continue
			fieldname = str(field.get("fieldname") or "")
			child_doctype = str(field.get("options") or "")
			if not fieldname or not child_doctype:
				continue
			if not frappe.db.table_exists(child_doctype, cached=False):
				continue

			base_filters = {
				"parentfield": fieldname,
				"parenttype": ["in", [legacy_name, target_name]],
			}
			legacy_rows = frappe.get_all(
				child_doctype,
				filters={**base_filters, "parent": legacy_name},
				fields=["*"],
				order_by="idx asc, name asc",
				limit=0,
			)
			if not legacy_rows:
				continue
			target_rows = frappe.get_all(
				child_doctype,
				filters={
					"parent": target_name,
					"parenttype": target_name,
					"parentfield": fieldname,
				},
				fields=["*"],
				order_by="idx asc, name asc",
				limit=0,
			)
			columns = set(frappe.db.get_table_columns(child_doctype))
			comparison_fields = sorted(columns - _CHILD_IDENTITY_COLUMNS)
			target_signatures = Counter(
				_child_business_signature(row, comparison_fields) for row in target_rows
			)
			duplicate_names: list[str] = []
			rows_to_move = []
			for row in legacy_rows:
				signature = _child_business_signature(row, comparison_fields)
				if target_signatures[signature]:
					target_signatures[signature] -= 1
					duplicate_names.append(str(row.name))
				else:
					rows_to_move.append(row)

			if duplicate_names:
				frappe.db.delete(child_doctype, {"name": ["in", duplicate_names]})
				result["deduplicated"] += len(duplicate_names)
			next_idx = max((int(row.idx or 0) for row in target_rows), default=0)
			for row in rows_to_move:
				next_idx += 1
				frappe.db.set_value(
					child_doctype,
					row.name,
					{
						"parent": target_name,
						"parenttype": target_name,
						"parentfield": fieldname,
						"idx": next_idx,
					},
					update_modified=False,
				)
				result["moved"] += 1
	return result


_CHILD_IDENTITY_COLUMNS = {
	"name",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"docstatus",
	"idx",
	"parent",
	"parentfield",
	"parenttype",
	"_user_tags",
	"_comments",
	"_assign",
	"_liked_by",
}


def _child_business_signature(row, fields: list[str]) -> str:
	return json.dumps(
		{field: row.get(field) for field in fields},
		sort_keys=True,
		separators=(",", ":"),
		default=str,
	)


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
