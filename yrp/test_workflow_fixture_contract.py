import json
import unittest
from pathlib import Path

from yrp import hooks


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fixture_names(filename):
	return {
		doc["name"]
		for doc in json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))
	}


def _hook_names(doctype):
	fixture = _hook_fixture(doctype)
	return set(fixture["filters"][0][2])


def _hook_fixture(doctype):
	return next(row for row in hooks.fixtures if row["dt"] == doctype)


class TestWorkflowFixtureContract(unittest.TestCase):
	def test_master_fixtures_cover_every_referenced_state_and_action(self):
		workflows = json.loads((FIXTURES_DIR / "workflow.json").read_text(encoding="utf-8"))
		referenced_states = {
			state["state"]
			for workflow in workflows
			for state in workflow.get("states", [])
		}
		referenced_actions = {
			transition["action"]
			for workflow in workflows
			for transition in workflow.get("transitions", [])
		}

		self.assertLessEqual(referenced_states, _fixture_names("00_workflow_state.json"))
		self.assertLessEqual(referenced_states, _hook_names("Workflow State"))
		self.assertLessEqual(referenced_actions, _fixture_names("01_workflow_action_master.json"))
		self.assertLessEqual(referenced_actions, _hook_names("Workflow Action Master"))

	def test_master_fixtures_import_before_workflows(self):
		self.assertEqual(_hook_fixture("Workflow State")["prefix"], "00")
		self.assertEqual(_hook_fixture("Workflow Action Master")["prefix"], "01")
		fixture_files = sorted(path.name for path in FIXTURES_DIR.glob("*.json"))
		self.assertLess(fixture_files.index("00_workflow_state.json"), fixture_files.index("workflow.json"))
		self.assertLess(fixture_files.index("01_workflow_action_master.json"), fixture_files.index("workflow.json"))
