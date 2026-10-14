import frappe

from yrp.web_reports.jobs import download_report_job as _download_report_job
from yrp.web_reports.jobs import get_report_job as _get_report_job
from yrp.web_reports.jobs import run_web_report as _run_web_report
from yrp.web_reports.layouts import (
	get_report_layout as _get_report_layout,
)
from yrp.web_reports.layouts import (
	reset_report_layout as _reset_report_layout,
)
from yrp.web_reports.layouts import (
	save_report_layout as _save_report_layout,
)
from yrp.web_reports.link_columns import (
	get_link_field_options as _get_link_field_options,
)
from yrp.web_reports.link_columns import (
	get_linked_values as _get_linked_values,
)


@frappe.whitelist()
def run_web_report(report_key, filters=None, refresh=0):
	return _run_web_report(report_key, filters, refresh)


@frappe.whitelist()
def get_report_job(report_key, job_name, include_result=0):
	return _get_report_job(report_key, job_name, include_result)


@frappe.whitelist()
def download_report_job(report_key, job_name):
	return _download_report_job(report_key, job_name)


@frappe.whitelist()
def get_report_layout(report_key):
	return _get_report_layout(report_key)


@frappe.whitelist()
def save_report_layout(report_key, columns=None):
	return _save_report_layout(report_key, columns)


@frappe.whitelist()
def reset_report_layout(report_key):
	return _reset_report_layout(report_key)


@frappe.whitelist()
def get_link_field_options(report_key, source_column):
	return _get_link_field_options(report_key, source_column)


@frappe.whitelist()
def get_linked_values(report_key, requests=None):
	return _get_linked_values(report_key, requests)
