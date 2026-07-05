"""Stock Entry — primary stock transaction (issue/receipt/transfer/transit).

Dimensions on Stock Entry Detail are added by the YRP Stock dimension patch.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class StockEntry(Document):
	def onload(self):
		from yrp.stock.save_stock_items import group_items_for_ui

		grouped = group_items_for_ui(self.get("items") or [], "Stock Entry")
		self.set_onload("item_details", grouped)

	def before_validate(self):
		from yrp.stock.save_stock_items import ungroup_items_from_ui
		from yrp.stock.dimensions import apply_dimension_defaults

		if self.get("item_details") and self._action != "submit":
			rows = ungroup_items_from_ui(self.item_details, "Stock Entry")
			self.set("items", [])
			for r in rows:
				self.append("items", r)
			self.set_rate_from_last_sle()
			self.set_receive_links()
		apply_dimension_defaults(self.get("items") or [])

	def set_rate_from_last_sle(self):
		from yrp.stock.utils import get_last_sle_rate
		from yrp.stock.dimensions import get_dimension_fieldnames

		dim_fields = get_dimension_fieldnames()
		# Source warehouse for outgoing purposes; target for incoming receipts.
		warehouse = self.from_warehouse or self.to_warehouse
		for row in self.items:
			dim_filters = {fn: row.get(fn) for fn in dim_fields}
			rate, _matched = get_last_sle_rate(row.item, warehouse=warehouse, **dim_filters)
			row.rate = flt(rate)

	def set_receive_links(self):
		"""For Receive at Warehouse, re-establish against_stock_entry and ste_detail
		after ungroup rebuilds the child rows (which wipes mapped fields)."""
		if self.purpose != "Receive at Warehouse" or not self.outgoing_stock_entry:
			return

		from yrp.stock.dimensions import get_dimension_fieldnames

		source_doc = frappe.get_doc("Stock Entry", self.outgoing_stock_entry)
		dim_fields = get_dimension_fieldnames()

		# Build lookup: (item, dim1, dim2, ...) → [source_row_name, ...]
		source_map = {}
		for row in source_doc.items:
			key = (row.item,) + tuple(row.get(fn) for fn in dim_fields)
			source_map.setdefault(key, []).append(row.name)

		for row in self.items:
			row.against_stock_entry = self.outgoing_stock_entry
			key = (row.item,) + tuple(row.get(fn) for fn in dim_fields)
			matches = source_map.get(key, [])
			if matches:
				row.ste_detail = matches.pop(0)

	def validate(self):
		self.validate_warehouses()
		self.validate_items()
		self.calculate_totals()
		self.validate_production_consumption_received_type()
		self.validate_dc_completion_pending()
		self.validate_grn_completion_pending()

	def validate_grn_completion_pending(self):
		if self.purpose != "GRN Completion" or self.against != "Goods Received Note" or not self.against_id:
			return
		for row in self.items:
			if not row.against_id_detail:
				frappe.throw(_("Row {0}: against_id_detail is required for GRN Completion.").format(row.idx))
			received, ste_done = frappe.db.get_value(
				"Goods Received Note Item",
				row.against_id_detail,
				["quantity", "ste_received_quantity"],
			) or (0, 0)
			pending = flt(received) - flt(ste_done)
			if flt(row.qty) > pending + 1e-6:
				frappe.throw(
					_("Row {0}: qty {1} exceeds remaining pending {2} on Goods Received Note Item {3}.").format(
						row.idx, flt(row.qty), pending, row.against_id_detail
					)
				)

	def validate_dc_completion_pending(self):
		if self.purpose != "DC Completion" or self.against != "Delivery Challan" or not self.against_id:
			return
		for row in self.items:
			if not row.against_id_detail:
				frappe.throw(_("Row {0}: against_id_detail is required for DC Completion.").format(row.idx))
			delivered, ste_done = frappe.db.get_value(
				"Delivery Challan Item",
				row.against_id_detail,
				["delivered_quantity", "ste_delivered_quantity"],
			) or (0, 0)
			pending = flt(delivered) - flt(ste_done)
			if flt(row.qty) > pending + 1e-6:
				frappe.throw(
					_("Row {0}: qty {1} exceeds remaining pending {2} on Delivery Challan Item {3}.").format(
						row.idx, flt(row.qty), pending, row.against_id_detail
					)
				)

	def validate_production_consumption_received_type(self):
		"""G.5 (partial, Gap #7): when a Stock Entry consumes for production
		(purpose='Material Consumed'), source lines must use the default
		Received Type. Quality-rejected stock cannot enter production.

		This rail is no-op until Received Type is registered as a dimension.
		"""
		if self.purpose != "Material Consumed":
			return
		default_rt = frappe.db.get_single_value(
			"YRP Stock Settings", "default_received_type"
		)
		if not default_rt:
			return
		from yrp.stock.dimensions import get_dimension_fieldnames

		if "received_type" not in get_dimension_fieldnames():
			return
		for row in self.items:
			rt = row.get("received_type")
			if rt and rt != default_rt:
				frappe.throw(
					_(
						"Row {0}: production consumption requires Received Type = {1}; "
						"got {2}. Reclassify the stock first via Inspection Entry."
					).format(row.idx, default_rt, rt)
				)

	def on_submit(self):
		from yrp.stock.stock_ledger import make_sl_entries

		sl_entries = self.get_sl_entries()
		make_sl_entries(sl_entries)
		self.update_transferred_qty()
		self.update_dc_completion(cancel=False)
		self.update_grn_completion(cancel=False)

	def before_cancel(self):
		self.ignore_linked_doctypes = ("Stock Ledger Entry", "Repost Item Valuation")

	def on_cancel(self):
		from yrp.stock.stock_ledger import make_sl_entries

		sl_entries = self.get_sl_entries(cancel=True)
		make_sl_entries(sl_entries, cancel=True)
		self.update_transferred_qty()
		self.update_dc_completion(cancel=True)
		self.update_grn_completion(cancel=True)

	# ------------------------------------------------------------------
	def validate_warehouses(self):
		needs_from = self.purpose in ("Material Issue", "Send to Warehouse", "Receive at Warehouse", "Material Consumed", "DC Completion", "GRN Completion")
		needs_to = self.purpose in ("Material Receipt", "Send to Warehouse", "Receive at Warehouse", "DC Completion", "GRN Completion")
		if needs_from and not self.from_warehouse:
			frappe.throw(_("From Warehouse is required for purpose {0}").format(self.purpose))
		if needs_to and not self.to_warehouse:
			frappe.throw(_("To Warehouse is required for purpose {0}").format(self.purpose))
		if self.purpose in ("Send to Warehouse", "Receive at Warehouse", "DC Completion", "GRN Completion"):
			if self.purpose in ("DC Completion", "GRN Completion") or not self.skip_transit:
				transit = frappe.db.get_single_value("YRP Stock Settings", "transit_warehouse")
				if not transit:
					frappe.throw(_("Transit Warehouse must be set in YRP Stock Settings for purpose {0}").format(self.purpose))

	def validate_items(self):
		if not self.items:
			frappe.throw(_("At least one item is required"))
		for row in self.items:
			if not row.qty or row.qty <= 0:
				frappe.throw(_("Row {0}: qty must be > 0").format(row.idx))
			if not row.uom:
				row.uom = frappe.db.get_value("Item Variant", row.item, "stock_uom")
			row.conversion_factor = row.conversion_factor or 1.0
			row.stock_qty = (row.qty or 0) * (row.conversion_factor or 1.0)
			row.amount = (row.qty or 0) * (row.rate or 0)

	def calculate_totals(self):
		self.total_amount = sum((r.amount or 0) for r in self.items)

	# ------------------------------------------------------------------
	def get_sl_entries(self, cancel=False):
		"""Build SL entry dicts for this Stock Entry.

		Material Issue          : -from_warehouse
		Material Receipt        : +to_warehouse
		Send to Warehouse       : -from_warehouse, +transit
		Receive at Warehouse    : -transit, +to_warehouse  (linked via outgoing_stock_entry)
		Material Consumed       : -from_warehouse
		DC Completion           : -transit, +to_warehouse  (linked via against=Delivery Challan)
		GRN Completion          : -transit, +to_warehouse  (linked via against=Goods Received Note)
		"""
		from yrp.stock.dimensions import get_stock_dimensions

		entries = []
		dim_fields = [d["fieldname"] for d in get_stock_dimensions()]
		transit_warehouse = frappe.db.get_single_value("YRP Stock Settings", "transit_warehouse")

		for row in self.items:
			base = {
				"item": row.item,
				"uom": row.uom,
				"voucher_type": "Stock Entry",
				"voucher_no": self.name,
				"voucher_detail_no": row.name,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"is_cancelled": 1 if cancel else 0,
			}
			for fn in dim_fields:
				base[fn] = row.get(fn)

			if self.purpose == "Material Issue":
				entries.append({**base, "warehouse": self.from_warehouse, "qty": -row.stock_qty, "rate": 0, "outgoing_rate": row.rate or 0})
			elif self.purpose == "Material Receipt":
				entries.append({**base, "warehouse": self.to_warehouse, "qty": row.stock_qty, "rate": row.rate or 0})
			elif self.purpose == "Send to Warehouse":
				entries.append({**base, "warehouse": self.from_warehouse, "qty": -row.stock_qty, "rate": 0, "outgoing_rate": row.rate or 0})
				if self.skip_transit:
					entries.append({**base, "warehouse": self.to_warehouse, "qty": row.stock_qty, "rate": row.rate or 0})
				else:
					entries.append({**base, "warehouse": transit_warehouse, "qty": row.stock_qty, "rate": row.rate or 0})
			elif self.purpose == "Receive at Warehouse":
				entries.append({**base, "warehouse": transit_warehouse, "qty": -row.stock_qty, "rate": 0, "outgoing_rate": row.rate or 0})
				entries.append({**base, "warehouse": self.to_warehouse, "qty": row.stock_qty, "rate": row.rate or 0})
			elif self.purpose == "Material Consumed":
				entries.append({**base, "warehouse": self.from_warehouse, "qty": -row.stock_qty, "rate": 0, "outgoing_rate": row.rate or 0})
			elif self.purpose == "DC Completion":
				if not transit_warehouse:
					frappe.throw(_("Transit Warehouse must be set in YRP Stock Settings for purpose DC Completion."))
				entries.append({**base, "warehouse": transit_warehouse, "qty": -row.stock_qty, "rate": 0, "outgoing_rate": row.rate or 0})
				entries.append({**base, "warehouse": self.to_warehouse, "qty": row.stock_qty, "rate": row.rate or 0})
			elif self.purpose == "GRN Completion":
				if not transit_warehouse:
					frappe.throw(_("Transit Warehouse must be set in YRP Stock Settings for purpose GRN Completion."))
				entries.append({**base, "warehouse": transit_warehouse, "qty": -row.stock_qty, "rate": 0, "outgoing_rate": row.rate or 0})
				entries.append({**base, "warehouse": self.to_warehouse, "qty": row.stock_qty, "rate": row.rate or 0})

		if cancel:
			# Reverse: flip qty and mark cancelled
			for e in entries:
				e["qty"] = -e["qty"]
		return entries

	# ------------------------------------------------------------------
	def update_transferred_qty(self):
		"""When a Receive at Warehouse entry is submitted/cancelled, update
		transferred_qty on the source Send to Warehouse entry rows and
		recalculate per_transferred on the source document."""
		if self.purpose != "Receive at Warehouse" or not self.outgoing_stock_entry:
			return

		source_doc = frappe.get_doc("Stock Entry", self.outgoing_stock_entry)
		for source_row in source_doc.items:
			transferred = frappe.db.sql(
				"""SELECT IFNULL(SUM(qty), 0)
				FROM `tabStock Entry Detail`
				WHERE against_stock_entry = %s
				AND ste_detail = %s
				AND docstatus = 1""",
				(self.outgoing_stock_entry, source_row.name),
			)[0][0]
			frappe.db.set_value("Stock Entry Detail", source_row.name, "transferred_qty", flt(transferred), update_modified=False)

		# Recalculate per_transferred
		source_doc.reload()
		total_qty = sum(flt(r.qty) for r in source_doc.items)
		total_transferred = sum(flt(r.transferred_qty) for r in source_doc.items)
		per_transferred = (total_transferred / total_qty * 100) if total_qty else 0
		frappe.db.set_value("Stock Entry", self.outgoing_stock_entry, "per_transferred", flt(per_transferred), update_modified=False)

	def update_dc_completion(self, cancel=False):
		"""Roll completion qty back into the source Delivery Challan.

		Submit:  +qty onto each DC Item's ste_delivered_quantity, +qty onto DC's
		         ste_transferred, recompute percent, flip transfer_complete if full.
		Cancel:  reverse all of the above; un-flip transfer_complete if it falls back.
		"""
		if self.purpose != "DC Completion" or self.against != "Delivery Challan" or not self.against_id:
			return

		sign = -1 if cancel else 1
		dc_name = self.against_id
		dc = frappe.get_doc("Delivery Challan", dc_name, for_update=True)

		transferred_delta = 0.0
		for row in self.items:
			if not row.against_id_detail:
				continue
			dc_item = next((i for i in dc.items if i.name == row.against_id_detail), None)
			if not dc_item:
				continue
			new_val = flt(dc_item.ste_delivered_quantity) + sign * flt(row.qty)
			frappe.db.set_value(
				"Delivery Challan Item",
				dc_item.name,
				"ste_delivered_quantity",
				new_val,
				update_modified=False,
			)
			transferred_delta += sign * flt(row.qty)

		# row.qty and DC.total_delivered_qty are both in UOM (not stock-uom); they sum cleanly.
		new_ste_transferred = flt(dc.ste_transferred) + transferred_delta
		total = flt(dc.total_delivered_qty) or 0
		new_percent = (new_ste_transferred / total * 100) if total else 0
		new_complete = 1 if total and (new_ste_transferred + 1e-6) >= total else 0

		frappe.db.set_value(
			"Delivery Challan",
			dc_name,
			{
				"ste_transferred": new_ste_transferred,
				"ste_transferred_percent": new_percent,
				"transfer_complete": new_complete,
			},
			update_modified=False,
		)

	def update_grn_completion(self, cancel=False):
		"""Roll completion qty back into the source Goods Received Note.

		Submit:  +qty onto each GRN Item's ste_received_quantity, +qty onto GRN's
		         ste_transferred, recompute percent, flip transfer_complete if full.
		Cancel:  reverse all of the above; un-flip transfer_complete if it falls back.
		"""
		if self.purpose != "GRN Completion" or self.against != "Goods Received Note" or not self.against_id:
			return

		sign = -1 if cancel else 1
		grn_name = self.against_id
		grn = frappe.get_doc("Goods Received Note", grn_name, for_update=True)

		transferred_delta = 0.0
		for row in self.items:
			if not row.against_id_detail:
				continue
			grn_item = next((i for i in grn.items if i.name == row.against_id_detail), None)
			if not grn_item:
				continue
			new_val = flt(grn_item.ste_received_quantity) + sign * flt(row.qty)
			frappe.db.set_value(
				"Goods Received Note Item",
				grn_item.name,
				"ste_received_quantity",
				new_val,
				update_modified=False,
			)
			transferred_delta += sign * flt(row.qty)

		new_ste_transferred = flt(grn.ste_transferred) + transferred_delta
		total = flt(grn.total_received_quantity) or 0
		new_percent = (new_ste_transferred / total * 100) if total else 0
		new_complete = 1 if total and (new_ste_transferred + 1e-6) >= total else 0

		frappe.db.set_value(
			"Goods Received Note",
			grn_name,
			{
				"ste_transferred": new_ste_transferred,
				"ste_transferred_percent": new_percent,
				"transfer_complete": new_complete,
			},
			update_modified=False,
		)


@frappe.whitelist()
def make_stock_in_entry(source_name, target_doc=None):
	"""Create a Receive at Warehouse entry from a submitted Send to Warehouse entry."""
	from frappe.model.mapper import get_mapped_doc
	from yrp.stock.save_stock_items import group_items_for_ui

	def set_missing_values(source, target):
		target.purpose = "Receive at Warehouse"
		target.outgoing_stock_entry = source.name

	def update_item(source_doc, target_doc, source_parent):
		target_doc.qty = flt(source_doc.qty) - flt(source_doc.transferred_qty)

	doclist = get_mapped_doc(
		"Stock Entry",
		source_name,
		{
			"Stock Entry": {
				"doctype": "Stock Entry",
				"field_map": {"name": "outgoing_stock_entry"},
				"validation": {"docstatus": ["=", 1]},
			},
			"Stock Entry Detail": {
				"doctype": "Stock Entry Detail",
				"field_map": {
					"name": "ste_detail",
					"parent": "against_stock_entry",
				},
				"postprocess": update_item,
				"condition": lambda doc: flt(doc.qty) - flt(doc.transferred_qty) > 0.01,
			},
		},
		target_doc,
		set_missing_values,
	)

	# Set onload item_details for the Vue editor
	grouped = group_items_for_ui(doclist.get("items") or [], "Stock Entry")
	doclist.set_onload("item_details", grouped)

	return doclist
