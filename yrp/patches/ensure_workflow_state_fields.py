# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe


WORKFLOWS = ("Item Price Workflow", "Process Cost Workflow")


def execute():
	for workflow_name in WORKFLOWS:
		if not frappe.db.exists("Workflow", workflow_name):
			continue

		workflow = frappe.get_doc("Workflow", workflow_name)
		if not workflow.document_type or not workflow.workflow_state_field:
			continue
		if not frappe.db.exists("DocType", workflow.document_type):
			continue

		workflow.create_custom_field_for_workflow_state()
		workflow.update_default_workflow_status()
