from collections.abc import Mapping
from typing import Any

import frappe
from frappe import _
from frappe.utils import date_diff

from yrp.web_reports.registry import filter_fieldtype

BACKGROUND_KEYS = {"days", "from_field", "to_field", "when", "missing_bound"}


def needs_background(entry, filters: Mapping[str, Any] | None) -> bool:
	config = validate_background(entry)
	if config == "always":
		return True
	if config == "never":
		return False

	filters = filters or {}
	if not _conditions_match(config.get("when", {}), filters):
		return False
	from_date = filters.get(config["from_field"])
	to_date = filters.get(config["to_field"])
	if not from_date or not to_date:
		return config["missing_bound"] == "background"
	return date_diff(to_date, from_date) > config["days"]


def validate_background(entry) -> str | dict[str, Any]:
	filters = _entry_value(entry, "filters") or {}
	config = _entry_value(entry, "background")
	if config is None:
		return _default_background(filters)
	if isinstance(config, str) and config in {"always", "never"}:
		return config
	if not isinstance(config, Mapping):
		frappe.throw(_("Background configuration must be 'always', 'never', or an object."))
	_validate_background_mapping(config, filters)
	return {
		"days": config["days"],
		"from_field": config["from_field"],
		"to_field": config["to_field"],
		"when": dict(config.get("when", {})),
		"missing_bound": config["missing_bound"],
	}


def _validate_background_mapping(config, filters) -> None:
	unknown = sorted(set(config) - BACKGROUND_KEYS)
	if unknown:
		frappe.throw(_("Unknown background configuration keys: {0}").format(", ".join(unknown)))
	_validate_required_keys(config)
	if isinstance(config["days"], bool) or not isinstance(config["days"], int) or config["days"] <= 0:
		frappe.throw(_("Background days must be a positive integer."))
	_validate_date_fields(config, filters)
	if config["missing_bound"] not in {"immediate", "background"}:
		frappe.throw(_("Background missing_bound must be immediate or background."))
	when = config.get("when", {})
	if not isinstance(when, Mapping) or any(key not in filters for key in when):
		frappe.throw(_("Background conditions must name declared filters."))


def _validate_date_fields(config, filters) -> None:
	for key in ("from_field", "to_field"):
		fieldname = config[key]
		if fieldname not in filters or filter_fieldtype(filters[fieldname]) != "Date":
			frappe.throw(_("Background {0} must name a Date filter.").format(key))


def _default_background(filters) -> str | dict[str, Any]:
	if all(name in filters and filter_fieldtype(filters[name]) == "Date" for name in ("from_date", "to_date")):
		return {
			"days": 30,
			"from_field": "from_date",
			"to_field": "to_date",
			"when": {},
			"missing_bound": "immediate",
		}
	return "never"


def _validate_required_keys(config) -> None:
	missing = [key for key in ("days", "from_field", "to_field", "missing_bound") if key not in config]
	if missing:
		frappe.throw(_("Missing background configuration keys: {0}").format(", ".join(missing)))


def _conditions_match(conditions, filters) -> bool:
	return all(_normalized(filters.get(key)) == _normalized(value) for key, value in conditions.items())


def _normalized(value) -> str:
	return str(value) if value is not None else ""


def _entry_value(entry, key):
	if isinstance(entry, Mapping):
		return entry.get(key)
	return getattr(entry, key, None)
