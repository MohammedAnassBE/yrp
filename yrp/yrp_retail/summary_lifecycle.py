"""Scoped permission for summary-owned source submission."""
from contextlib import contextmanager
from contextvars import ContextVar

_submitting = ContextVar("summary_submitting_order", default=None)


def submitting_order(order):
	return _submitting.get() is order


@contextmanager
def source_submission(order):
	token = _submitting.set(order)
	try:
		yield
	finally:
		_submitting.reset(token)
