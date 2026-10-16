"""Helpers for real multi-connection transaction tests."""

import frappe


def commit_connection():
	"""Commit only the disposable connection opened by a concurrency test."""
	frappe.db.commit()
