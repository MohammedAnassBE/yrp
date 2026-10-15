from frappe.desk.query_report import get_report_doc

from yrp.web_reports.registry import resolve_entry_path


def check_report_access(entry) -> None:
	if entry.kind == "frappe_report":
		get_report_doc(entry.report)
		return
	resolve_entry_path(entry, "access")()
