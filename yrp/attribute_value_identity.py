"""Stable, attribute-scoped value identity shared by migration and runtime."""

import hashlib
import json


def attribute_value_name(attribute, value):
	payload = json.dumps([str(attribute), str(value)], ensure_ascii=False, separators=(",", ":"))
	return "IAV-" + hashlib.sha256(payload.encode()).hexdigest()[:40]


def get_attribute_value_names(pairs):
	"""Return stable names keyed by normalized attribute/value pairs."""
	return {
		(str(attribute), str(value)): attribute_value_name(attribute, value) for attribute, value in pairs
	}


def clear_name_cache():
	"""Compatibility hook; stable names require no cache."""
