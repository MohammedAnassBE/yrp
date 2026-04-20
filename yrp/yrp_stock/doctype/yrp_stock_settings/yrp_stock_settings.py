# Copyright (c) 2026, Mohammed Anas and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class YRPStockSettings(Document):
	def validate(self):
		self.validate_production_group()
		self.validate_unique_fieldnames()
		self.validate_unique_doctypes()
		self.validate_dimension_doctypes_exist()

	def on_update(self):
		from yrp.stock.dimensions import clear_dimension_cache
		clear_dimension_cache()

	def validate_production_group(self):
		"""Only one dimension can have is_production_group = 1."""
		production_groups = [d for d in self.stock_dimensions if d.is_production_group]
		if len(production_groups) > 1:
			frappe.throw("Only one stock dimension can be marked as Production Group.")

	def validate_unique_fieldnames(self):
		"""Ensure no duplicate fieldnames in stock dimensions."""
		fieldnames = []
		for d in self.stock_dimensions:
			if d.fieldname in fieldnames:
				frappe.throw(f"Duplicate fieldname '{d.fieldname}' in Stock Dimensions.")
			fieldnames.append(d.fieldname)

	def validate_unique_doctypes(self):
		"""Ensure no duplicate dimension doctypes."""
		doctypes = []
		for d in self.stock_dimensions:
			if d.dimension_doctype in doctypes:
				frappe.throw(f"Duplicate DocType '{d.dimension_doctype}' in Stock Dimensions.")
			doctypes.append(d.dimension_doctype)

	def validate_dimension_doctypes_exist(self):
		"""Ensure each configured dimension DocType actually exists."""
		for d in self.stock_dimensions:
			if not frappe.db.exists("DocType", d.dimension_doctype):
				frappe.throw(f"DocType '{d.dimension_doctype}' does not exist. "
					"Create the DocType before adding it as a stock dimension.")
