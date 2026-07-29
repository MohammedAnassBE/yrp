"""Regression checks that keep YRP tests inside the runner transaction."""

import ast
from pathlib import Path

from frappe.tests import UnitTestCase


def _dotted_name(node):
	if isinstance(node, ast.Name):
		return node.id
	if isinstance(node, ast.Attribute):
		parent = _dotted_name(node.value)
		return f"{parent}.{node.attr}" if parent else node.attr
	return ""


class TestTestTransactionSafety(UnitTestCase):
	def test_test_modules_do_not_commit_database_transactions(self):
		offenders = []
		package_root = Path(__file__).parent

		for path in package_root.rglob("test*.py"):
			tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
			for node in ast.walk(tree):
				if not isinstance(node, ast.Call):
					continue
				call_name = _dotted_name(node.func)
				if call_name in {"frappe.db.commit", "db.commit"}:
					offenders.append(f"{path.relative_to(package_root)}:{node.lineno}")

		self.assertEqual(
			offenders,
			[],
			"Test code must rely on Frappe's rollback, not commit shared site data: "
			+ ", ".join(offenders),
		)
