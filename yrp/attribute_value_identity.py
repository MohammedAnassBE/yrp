"""Stable, attribute-scoped value identity shared by migration and runtime."""
import hashlib
import json


def attribute_value_name(attribute, value):
	payload = json.dumps([str(attribute), str(value)], ensure_ascii=False, separators=(",", ":"))
	return "IAV-" + hashlib.sha256(payload.encode()).hexdigest()[:40]

