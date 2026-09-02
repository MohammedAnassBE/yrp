import json
from pathlib import Path

import frappe

from yrp.namespace_migration import (
	rename_owned_customization_records,
	rewrite_owned_doctype_discriminators,
)


PREFIX = "YRP "
OWNED_MODULES = {"YRP", "YRP Stock"}


def _metadata_renames():
	app_path = Path(frappe.get_app_path("yrp"))
	records = []
	for path in sorted(app_path.rglob("*.json")):
		try:
			data = json.loads(path.read_text())
		except (OSError, UnicodeDecodeError, json.JSONDecodeError):
			continue
		if not isinstance(data, dict):
			continue

		if data.get("doctype") == "DocType" and data.get("name", "").startswith(PREFIX):
			# Rename parent DocTypes before their child tables. Reports are last so
			# every ref_doctype already points at its final name.
			priority = 1 if data.get("istable") else 0
			records.append(
				(priority, "DocType", data["name"].removeprefix(PREFIX), data["name"])
			)
		elif data.get("doctype") == "Report":
			report_name = data.get("report_name") or data.get("name") or ""
			if not report_name.startswith(PREFIX):
				continue
			records.append(
				(2, "Report", report_name.removeprefix(PREFIX), report_name)
			)

	for _priority, record_type, old_name, new_name in sorted(records):
		yield record_type, old_name, new_name


def _rename_owned_record(record_type, old_name, new_name):
	old_module = frappe.db.get_value(record_type, old_name, "module")
	new_module = frappe.db.get_value(record_type, new_name, "module")

	if new_module:
		if old_module in OWNED_MODULES:
			frappe.throw(
				f"Both {record_type} {old_name} and {new_name} exist during the YRP namespace migration"
			)
		return
	if old_module not in OWNED_MODULES:
		return

	frappe.rename_doc(
		record_type,
		old_name,
		new_name,
		force=True,
		show_alert=False,
		rebuild_search=False,
	)


def execute():
	for record_type, old_name, new_name in _metadata_renames():
		_rename_owned_record(record_type, old_name, new_name)
	rewrite_owned_doctype_discriminators(("yrp",))
	rename_owned_customization_records(("yrp",))
	frappe.clear_cache()
