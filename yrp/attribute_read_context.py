"""Detached lookup reuse inside explicitly read-only builders."""

from contextlib import ContextDecorator
from contextvars import ContextVar
from copy import deepcopy

import frappe

_reads = ContextVar("yrp_attribute_reads", default=None)


class attribute_read_scope(ContextDecorator):
	def __init__(self):
		self.tokens = []

	def __enter__(self):
		self.tokens.append(_reads.set({}) if _reads.get() is None else None)
		return self

	def __exit__(self, *exc):
		token = self.tokens.pop()
		if token is not None:
			_reads.reset(token)

	def _recreate_cm(self):
		return type(self)()


def read_value(namespace, key, load):
	cache = _reads.get()
	if cache is None:
		return load()
	key = (frappe.local.site, frappe.session.user, namespace, key)
	if key not in cache:
		cache[key] = deepcopy(load())
	return deepcopy(cache[key])
