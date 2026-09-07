import json
import unittest
from pathlib import Path


class YRPBinSchemaTest(unittest.TestCase):
	def test_reserved_qty_is_a_visible_base_field(self):
		path = Path(__file__).with_name("yrp_bin.json")
		schema = json.loads(path.read_text())
		fields = {field["fieldname"]: field for field in schema["fields"]}
		reserved = fields["reserved_qty"]
		self.assertEqual(reserved["label"], "Reserved Qty")
		self.assertEqual(reserved["fieldtype"], "Float")
		self.assertFalse(reserved.get("hidden", 0))
		self.assertLess(
			schema["field_order"].index("actual_qty"),
			schema["field_order"].index("reserved_qty"),
		)

	def test_obsolete_field_removal_patch_is_not_scheduled(self):
		patches = Path(__file__).resolve().parents[3] / "patches.txt"
		contents = patches.read_text()
		self.assertNotIn("drop_bin_reserved_qty", contents)

	def test_yrp_runtime_files_are_source_neutral(self):
		package_root = Path(__file__).resolve().parents[3]
		former_source_name = "production" + "_api"
		matches = []
		for path in package_root.rglob("*"):
			if path.suffix not in {".py", ".js", ".json"} or "__pycache__" in path.parts:
				continue
			if former_source_name in path.read_text(encoding="utf-8").lower():
				matches.append(str(path.relative_to(package_root)))
		self.assertEqual(matches, [])
