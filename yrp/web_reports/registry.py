from dataclasses import dataclass, field, replace
from typing import Any, Mapping

import frappe
from frappe import _


@dataclass(slots=True)
class WebReport:
	key: str
	kind: str
	filters: dict[str, Any]
	link_sources: dict[str, str] = field(default_factory=dict)
	base_columns: dict[str, str] = field(default_factory=dict)
	report: str | None = None
	runner: str | None = None
	access: str | None = None
	permission_doctypes: tuple[str, ...] = ()
	dependency_doctypes: str | None = None
	background: str | dict[str, Any] | None = None

	def __getitem__(self, fieldname: str):
		return getattr(self, fieldname)

	def get(self, fieldname: str, default=None):
		return getattr(self, fieldname, default)


def get_web_reports() -> dict[str, WebReport]:
	"""Return the request-local registry from every installed app."""

	overrides = _test_overrides()
	cache_key = id(overrides) if overrides is not None else None
	cached = getattr(frappe.local, "yrp_web_reports_cache", None)
	if cached and cached[0] == cache_key:
		return cached[1]

	reports: dict[str, WebReport] = {}
	for path in _hook_paths(frappe.get_hooks("yrp_web_reports")):
		provider = _resolve_path(path)
		_add_entries(reports, provider(), replace_existing=False)
	_add_test_entries(reports)
	if overrides is not None:
		_add_entries(reports, overrides, replace_existing=True)

	frappe.local.yrp_web_reports_cache = (cache_key, reports)
	return reports


def get_web_report(report_key: str) -> WebReport:
	entry = get_web_reports().get(report_key)
	if not entry:
		frappe.throw(_("Unknown web report: {0}").format(report_key))
	return entry


def clear_web_report_cache() -> None:
	if hasattr(frappe.local, "yrp_web_reports_cache"):
		del frappe.local.yrp_web_reports_cache


def resolve_entry_path(entry: WebReport, fieldname: str):
	path = getattr(entry, fieldname)
	if not path:
		return None
	return _resolve_path(path)


def filter_fieldtype(specification: Any) -> str:
	if isinstance(specification, str):
		return specification
	return specification[0]


def filter_options(specification: Any) -> str | None:
	if isinstance(specification, (tuple, list)) and len(specification) == 2:
		return specification[1]
	return None


def _test_overrides():
	if not getattr(frappe.flags, "in_test", False):
		return None
	return getattr(frappe.flags, "yrp_web_report_overrides", None)


def _add_test_entries(reports) -> None:
	if not frappe.conf.get("allow_tests"):
		return
	base_entries = list(reports.values())
	for entry in base_entries:
		for suffix, background in (("test-never", "never"), ("test-always", "always")):
			clone = replace(entry, key=f"{entry.key}--{suffix}", background=background)
			reports[clone.key] = clone
	failed = WebReport(
		key="web-report--test-failing",
		kind="callable",
		filters={},
		runner="yrp.web_reports.test_support.fail_report",
		access="yrp.web_reports.test_support.allow_report_access",
		background="always",
	)
	reports[failed.key] = _normalize_entry(failed)


def _hook_paths(values):
	for value in values or []:
		if isinstance(value, (list, tuple)):
			yield from _hook_paths(value)
		elif value:
			yield value


def _resolve_path(path: str):
	if not isinstance(path, str) or "." not in path:
		frappe.throw(_("Invalid web report dotted path: {0}").format(path))
	try:
		return frappe.get_attr(path)
	except (AttributeError, ImportError, ModuleNotFoundError) as exception:
		raise frappe.ValidationError(_("Cannot resolve web report path: {0}").format(path)) from exception


def _add_entries(reports, entries, *, replace_existing: bool) -> None:
	for entry in _iter_entries(entries):
		normalized = _normalize_entry(entry)
		if normalized.key in reports and not replace_existing:
			frappe.throw(_("Duplicate web report key: {0}").format(normalized.key))
		reports[normalized.key] = normalized


def _iter_entries(entries):
	if isinstance(entries, WebReport):
		yield entries
		return
	if isinstance(entries, Mapping):
		if "key" in entries:
			yield entries
			return
		for key, entry in entries.items():
			if isinstance(entry, WebReport):
				yield replace(entry, key=key)
			else:
				yield {"key": key, **entry}
		return
	if not isinstance(entries, (list, tuple)):
		frappe.throw(_("Web report providers must return an entry, list, or mapping."))
	yield from entries


def _normalize_entry(entry) -> WebReport:
	if isinstance(entry, WebReport):
		entry = replace(entry)
	else:
		try:
			entry = WebReport(**dict(entry))
		except (TypeError, ValueError) as exception:
			raise frappe.ValidationError(_("Invalid web report entry: {0}").format(exception)) from exception

	_validate_entry(entry)
	from yrp.web_reports.background import validate_background

	entry.background = validate_background(entry)
	entry.permission_doctypes = tuple(entry.permission_doctypes)
	entry.filters = dict(entry.filters)
	entry.link_sources = dict(entry.link_sources)
	entry.base_columns = _normalize_base_columns(entry.base_columns)
	return entry


def _validate_entry(entry: WebReport) -> None:
	_validate_entry_identity(entry)
	_validate_entry_schema(entry)
	_validate_entry_paths(entry)


def _validate_entry_identity(entry: WebReport) -> None:
	if not entry.key or not isinstance(entry.key, str):
		frappe.throw(_("A web report key is required."))
	if entry.kind not in {"frappe_report", "callable"}:
		frappe.throw(_("Invalid web report kind for {0}.").format(entry.key))
	if entry.kind == "frappe_report" and not entry.report:
		frappe.throw(_("Report name is required for {0}.").format(entry.key))
	if entry.kind == "callable" and (not entry.runner or not entry.access):
		frappe.throw(_("Callable report {0} requires runner and access paths.").format(entry.key))


def _validate_entry_schema(entry: WebReport) -> None:
	if not isinstance(entry.filters, Mapping):
		frappe.throw(_("Filters for {0} must be a mapping.").format(entry.key))
	for fieldname, specification in entry.filters.items():
		_validate_filter(entry.key, fieldname, specification)
	if not isinstance(entry.link_sources, Mapping):
		frappe.throw(_("Link sources for {0} must be a mapping.").format(entry.key))


def _validate_entry_paths(entry: WebReport) -> None:
	for fieldname in ("runner", "access", "dependency_doctypes"):
		if getattr(entry, fieldname):
			_resolve_path(getattr(entry, fieldname))


def _validate_filter(report_key: str, fieldname: Any, specification: Any) -> None:
	if not isinstance(fieldname, str) or not fieldname:
		frappe.throw(_("Invalid filter name for {0}.").format(report_key))
	if isinstance(specification, str):
		return
	if not isinstance(specification, (tuple, list)) or len(specification) != 2:
		frappe.throw(_("Invalid filter schema for {0}.{1}.").format(report_key, fieldname))
	if specification[0] not in {"Link", "MultiSelectList"} or not isinstance(specification[1], str):
		frappe.throw(_("Invalid Link filter schema for {0}.{1}.").format(report_key, fieldname))


def _normalize_base_columns(columns) -> dict[str, str]:
	if isinstance(columns, Mapping):
		return {str(fieldname): str(label) for fieldname, label in columns.items()}
	result = {}
	for column in columns or []:
		if isinstance(column, str):
			result[column] = column
		else:
			result[column["fieldname"]] = column.get("label") or column["fieldname"]
	return result
