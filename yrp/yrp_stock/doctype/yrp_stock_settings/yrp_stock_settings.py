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
		self.validate_dimension_safety_rails()

	# --------------------------------------------------------------
	# Safety rails (D-003 / Gaps #3, #4, #5)
	# --------------------------------------------------------------
	def validate_dimension_safety_rails(self):
		"""Block dimension removal / rename / is_production_group switch when
		stock data already exists for that dimension."""
		if self.is_new():
			return
		previous = self._previous_dimension_rows()
		current = {d.fieldname: d for d in (self.stock_dimensions or [])}

		# C.1 — Removal blocked when SLE/Bin data exists.
		for fieldname, prev_row in previous.items():
			if fieldname in current:
				continue
			if self._dimension_has_data(fieldname):
				frappe.throw(
					f"Cannot remove dimension '{fieldname}' — Stock Ledger Entry "
					"or Bin rows still reference it. Run a migration patch first."
				)

		# C.2 — Fieldname rename blocked when data exists.
		# Match prior rows by row.name (Frappe child row id) to detect renames.
		for current_row in (self.stock_dimensions or []):
			prior = next(
				(p for p in previous.values() if getattr(p, "name", None) == getattr(current_row, "name", None)),
				None,
			)
			if not prior:
				continue
			if prior.fieldname != current_row.fieldname:
				if self._dimension_has_data(prior.fieldname):
					frappe.throw(
						f"Cannot rename dimension '{prior.fieldname}' to "
						f"'{current_row.fieldname}' — existing data references the old "
						"fieldname. Run a migration patch."
					)

			# C.3 — is_production_group switch blocked when any operational
			# doctype has the field populated.
			if bool(prior.is_production_group) != bool(current_row.is_production_group):
				if self._production_group_in_use(prior.fieldname):
					frappe.throw(
						f"Cannot toggle Production Group on '{prior.fieldname}' — "
						"operational documents already carry this field."
					)

	def _previous_dimension_rows(self):
		rows = frappe.get_all(
			"YRP Stock Dimension",
			filters={"parent": self.name, "parenttype": self.doctype},
			fields=["name", "fieldname", "is_production_group"],
		)
		return {r["fieldname"]: frappe._dict(r) for r in rows}

	@staticmethod
	def _dimension_has_data(fieldname):
		"""True if any SLE or Bin row has a non-null/non-empty value for this dim."""
		from yrp.stock.dimensions import assert_safe_fieldname

		assert_safe_fieldname(fieldname)
		for table in ("Stock Ledger Entry", "Bin"):
			cols = frappe.db.sql(
				f"SHOW COLUMNS FROM `tab{table}` LIKE %s", fieldname
			)
			if not cols:
				continue
			row = frappe.db.sql(
				f"""
				SELECT 1 FROM `tab{table}`
				WHERE `{fieldname}` IS NOT NULL AND `{fieldname}` != ''
				LIMIT 1
				"""
			)
			if row:
				return True
		return False

	@staticmethod
	def _production_group_in_use(fieldname):
		"""True if any operational DocType (WO/DC/GRN — when present) carries
		a populated value for this dimension."""
		from yrp.stock.dimensions import OPERATIONAL_DOCTYPES, assert_safe_fieldname

		assert_safe_fieldname(fieldname)
		for dt in OPERATIONAL_DOCTYPES:
			if not frappe.db.exists("DocType", dt):
				continue
			table = f"tab{dt}"
			cols = frappe.db.sql(f"SHOW COLUMNS FROM `{table}` LIKE %s", fieldname)
			if not cols:
				continue
			row = frappe.db.sql(
				f"SELECT 1 FROM `{table}` WHERE `{fieldname}` IS NOT NULL AND `{fieldname}` != '' LIMIT 1"
			)
			if row:
				return True
		return False

	def on_update(self):
		"""Bug A (r-010 Critical #4): when a new dimension is added, create
		the Custom Field on every stock DocType immediately. Without this,
		the engine sees the new dimension in the cache before its column
		exists in the DB, and the next stock transaction crashes with
		'Unknown column'.
		"""
		from yrp.stock.dimensions import clear_dimension_cache, create_dimension_fields

		clear_dimension_cache()
		create_dimension_fields()

	def validate_production_group(self):
		"""Only one dimension can have is_production_group = 1."""
		production_groups = [d for d in self.stock_dimensions if d.is_production_group]
		if len(production_groups) > 1:
			frappe.throw("Only one stock dimension can be marked as Production Group.")

	def validate_unique_fieldnames(self):
		"""Ensure no duplicate fieldnames in stock dimensions and that every
		fieldname is safe for SQL interpolation."""
		from yrp.stock.dimensions import assert_safe_fieldname

		fieldnames = []
		for d in self.stock_dimensions:
			# Defense-in-depth: the engine builds raw SQL with these names.
			assert_safe_fieldname(d.fieldname)
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
