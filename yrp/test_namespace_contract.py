"""Static contract tests for the YRP DocType/report namespace."""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call
from unittest.mock import patch

from yrp.patches import prefix_owned_doctypes_and_reports


PACKAGE_ROOT = Path(__file__).resolve().parent
OWNED_MODULES = {"YRP", "YRP Stock"}
PREFIX = "YRP "
LINK_FIELD_TYPES = {"Link", "Table", "Table MultiSelect"}


def _load_json(path: Path):
	try:
		return json.loads(path.read_text())
	except (OSError, UnicodeDecodeError, json.JSONDecodeError):
		return None


def _scrub(value: str) -> str:
	return re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()


def _owned_metadata(record_type: str):
	for path in PACKAGE_ROOT.rglob("*.json"):
		data = _load_json(path)
		if not isinstance(data, dict) or data.get("module") not in OWNED_MODULES:
			continue
		if data.get("doctype") != record_type:
			continue
		name = data.get("name")
		if record_type == "Report":
			name = data.get("report_name") or name
		if name:
			yield path, data, name


class TestNamespaceContract(unittest.TestCase):
	def test_pre_model_sync_patch_covers_the_complete_manifest(self):
		with patch.object(
			prefix_owned_doctypes_and_reports.frappe,
			"get_app_path",
			return_value=str(PACKAGE_ROOT),
		):
			records = list(prefix_owned_doctypes_and_reports._metadata_renames())

		self.assertEqual(len(records), 138)
		self.assertEqual(
			len({(record_type, new_name) for record_type, _old_name, new_name in records}),
			138,
		)

	def test_owned_rename_uses_frappe_v16_arguments(self):
		db = SimpleNamespace(
			get_value=lambda _record_type, name, _fieldname: {
				"Production Term": "YRP",
				"YRP Production Term": None,
			}[name]
		)
		with (
			patch.object(prefix_owned_doctypes_and_reports.frappe, "db", db),
			patch.object(prefix_owned_doctypes_and_reports.frappe, "rename_doc") as rename_doc,
		):
			prefix_owned_doctypes_and_reports._rename_owned_record(
				"DocType",
				"Production Term",
				"YRP Production Term",
			)

		self.assertEqual(
			rename_doc.call_args,
			call(
				"DocType",
				"Production Term",
				"YRP Production Term",
				force=True,
				show_alert=False,
				rebuild_search=False,
			),
		)

	def test_every_owned_doctype_and_report_has_the_yrp_prefix_and_path(self):
		counts = {}
		for record_type in ("DocType", "Report"):
			records = list(_owned_metadata(record_type))
			counts[record_type] = len(records)
			for path, _data, name in records:
				with self.subTest(record_type=record_type, name=name):
					slug = _scrub(name)
					self.assertTrue(name.startswith(PREFIX), name)
					self.assertEqual(path.parent.name, slug)
					self.assertEqual(path.name, f"{slug}.json")

		self.assertEqual(counts, {"DocType": 131, "Report": 7})

	def test_owned_link_and_table_targets_never_use_an_old_name(self):
		doctypes = list(_owned_metadata("DocType"))
		old_names = {name.removeprefix(PREFIX) for _path, _data, name in doctypes}
		for _path, data, name in doctypes:
			for field in data.get("fields") or []:
				if field.get("fieldtype") not in LINK_FIELD_TYPES:
					continue
				with self.subTest(doctype=name, fieldname=field.get("fieldname")):
					self.assertNotIn(field.get("options"), old_names)

	def test_controller_class_and_legacy_import_alias_match_the_new_name(self):
		for path, _data, name in _owned_metadata("DocType"):
			slug = _scrub(name)
			controller = path.with_name(f"{slug}.py")
			if not controller.exists():
				continue
			tree = ast.parse(controller.read_text(), filename=str(controller))
			new_class = name.replace(" ", "").replace("-", "")
			old_class = name.removeprefix(PREFIX).replace(" ", "").replace("-", "")
			classes = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
			aliases = {
				(target.id, node.value.id)
				for node in ast.walk(tree)
				if isinstance(node, ast.Assign)
				and isinstance(node.value, ast.Name)
				for target in node.targets
				if isinstance(target, ast.Name)
			}
			with self.subTest(doctype=name):
				self.assertIn(new_class, classes)
				self.assertIn((old_class, new_class), aliases)


if __name__ == "__main__":
	unittest.main()
