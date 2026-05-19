"""Inspection Entry: classify GRN-received stock into one or more Received
Types within the same warehouse + lot. Writes its own SLEs (does NOT route
through Stock Entry — see lessons-learned 2026-05-18).

UI is a Vue editor (`InspectionEntryEditor.vue`) mounted in the `item_html`
field. The editor groups child rows by source bin (one card per GRN line)
and lets the operator split into N target Received Types whose qtys must
sum back to the source qty.

Data flow:
  GRN → get_initial_payload() → grouped JSON → Vue → item_details (JSON) →
  before_validate flattens back into self.items child rows → existing
  validation / SLE construction operates on self.items unchanged.
"""

import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate, nowtime



class InspectionEntry(Document):
	def onload(self):
		"""Build grouped UI payload from saved child rows for the Vue editor."""
		grouped = _group_items_for_ui(self.get("items") or [])
		self.set_onload("item_details", grouped)

	def before_validate(self):
		if not self.posting_date:
			self.posting_date = nowdate()
		if not self.posting_time:
			self.posting_time = nowtime()

		# Rebuild self.items from the Vue editor's JSON.
		if self.get("item_details") and self._action != "submit":
			rows = _ungroup_items_from_ui(self.item_details)
			self.set("items", [])
			for r in rows:
				self.append("items", r)

	def validate(self):
		self._validate_source()
		self._validate_rows()
		self._validate_bin_balance()

	def before_submit(self):
		# Submitting the IE does NOT move stock — SLEs are only written when an
		# approver (role configured at YRP Settings.inspection_entry_approver_role)
		# clicks "Convert Stock". See `convert_stock` below.
		self.status = "Submitted"
		self._stamp_inspector()

	def on_submit(self):
		# Intentionally empty — no SLEs at submit. The IE is now locked for editing
		# but the underlying stock is unchanged.
		pass

	def before_cancel(self):
		# Once SLEs have been written via Convert Stock, the IE must not be
		# cancelled.
		if self.get("is_converted") or (self.status or "") == "Converted":
			frappe.throw(
				_(
					"Inspection Entry {0} has already converted stock and cannot be cancelled."
				).format(self.name)
			)
		self.ignore_linked_doctypes = ("Stock Ledger Entry", "Repost Item Valuation")
		self.status = "Cancelled"

	def on_cancel(self):
		# No SLEs exist for a not-yet-converted IE, so nothing to reverse.
		pass

	# ------------------------------------------------------------------
	# Validation helpers
	# ------------------------------------------------------------------
	def _validate_source(self):
		if self.against not in ("Goods Received Note", "Stock Entry"):
			frappe.throw(_("Inspection Entry supports Against = Goods Received Note or Stock Entry."))
		if not self.against_id:
			frappe.throw(_("Against ID is required."))
		if self.against == "Goods Received Note":
			docstatus = frappe.db.get_value("Goods Received Note", self.against_id, "docstatus")
			if docstatus != 1:
				frappe.throw(_("Goods Received Note {0} must be submitted.").format(self.against_id))
		else:  # Stock Entry
			row = frappe.db.get_value(
				"Stock Entry",
				self.against_id,
				["docstatus", "purpose"],
				as_dict=True,
			)
			if not row or row.docstatus != 1:
				frappe.throw(_("Stock Entry {0} must be submitted.").format(self.against_id))
			if (row.purpose or "") != "Material Receipt":
				frappe.throw(
					_("Stock Entry {0} must have Purpose = 'Material Receipt' to be inspected.").format(
						self.against_id
					)
				)

	def _validate_rows(self):
		if not self.items:
			frappe.throw(_("At least one item row is required."))
		for row in self.items:
			if flt(row.qty) < 0:
				frappe.throw(_("Row {0}: Qty cannot be negative.").format(row.idx))
			if not row.target_received_type:
				frappe.throw(_("Row {0}: Target Received Type is required.").format(row.idx))
			if not row.received_date:
				frappe.throw(_("Row {0}: Received Date is required.").format(row.idx))
			if not row.item_variant or not row.warehouse:
				frappe.throw(_("Row {0}: Item Variant and Warehouse are required.").format(row.idx))

	def _stamp_inspector(self):
		# Inspector is read-only on the form; stamped from the session at submit time.
		self.inspector = frappe.session.user

	def _bin_key(self, row):
		from yrp.stock.dimensions import get_dimension_fieldnames

		dims = tuple(row.get(fn) for fn in get_dimension_fieldnames())
		return (row.item_variant, row.warehouse, dims)

	# Note: cross-IE total cap was intentionally removed — multiple IEs against
	# the same GRN/Stock Entry are independent. The authoritative safeguard is
	# `_validate_bin_balance` + the stock engine's NegativeStockError at submit
	# time, which already prevent over-conversion at the bin level.

	def _validate_bin_balance(self):
		"""For each source bin, qty moving OUT of source (rows where target !=
		source received_type) must be ≤ current bin balance.

		Resolves a missing `received_type` on the row to the configured default
		so the balance query is correctly scoped to a single bin (not warehouse-
		wide). Without this fallback an unset received_type would silently make
		the query aggregate across all types and over-permit transfers.
		"""
		from yrp.stock.dimensions import get_dimension_fieldnames
		from yrp.stock.utils import get_stock_balance

		dim_fields = get_dimension_fieldnames()
		default_rt = frappe.db.get_single_value("YRP Stock Settings", "default_received_type")
		groups = defaultdict(list)
		for row in self.items:
			groups[self._bin_key(row)].append(row)
		for key, rows in groups.items():
			source_rt = rows[0].get("received_type") or default_rt
			out_qty = sum(
				flt(r.qty) for r in rows if r.target_received_type != source_rt
			)
			if out_qty <= 0:
				continue
			dim_filters = {}
			for fn in dim_fields:
				val = rows[0].get(fn)
				if fn == "received_type":
					val = val or default_rt
				if val is not None:
					dim_filters[fn] = val
			balance = get_stock_balance(
				rows[0].item_variant,
				rows[0].warehouse,
				**dim_filters,
			)
			if isinstance(balance, tuple):
				balance = balance[0]
			if out_qty > flt(balance) + 0.0001:
				frappe.throw(
					_("Item {0} at {1} ({2}): cannot reclassify {3} (only {4} available).").format(
						rows[0].item_variant,
						rows[0].warehouse,
						source_rt,
						out_qty,
						flt(balance),
					)
				)

	# ------------------------------------------------------------------
	# SLE construction — IE writes its own SLEs.
	# ------------------------------------------------------------------
	def _build_sl_entries(self, cancel=False):
		"""For each row where target != source received_type, emit:
		  -qty at (item, warehouse, lot, received_type=source)
		  +qty at (item, warehouse, lot, received_type=target)

		Rate is fetched from the last SLE at the source bin (FIFO/MA basis).
		Rows where target == source contribute no SLEs.
		"""
		from yrp.stock.dimensions import get_dimension_fieldnames
		from yrp.stock.utils import get_last_sle_rate

		dim_fields = get_dimension_fieldnames()
		entries = []
		for row in self.items:
			qty = flt(row.qty)
			if qty <= 0:
				continue
			source_rt = row.get("received_type")
			target_rt = row.target_received_type
			if source_rt == target_rt:
				continue

			parent_item = frappe.get_cached_value("Item Variant", row.item_variant, "item")
			stock_uom = (
				frappe.get_cached_value("Item", parent_item, "default_unit_of_measure")
				if parent_item
				else None
			)

			base = {
				"item": row.item_variant,
				"uom": stock_uom,
				"voucher_type": self.doctype,
				"voucher_no": self.name,
				"voucher_detail_no": row.name,
				"posting_date": self.posting_date,
				"posting_time": self.posting_time,
				"is_cancelled": 0,
			}
			dim_filters = {}
			for fn in dim_fields:
				val = row.get(fn) if row.meta.get_field(fn) else None
				base[fn] = val
				if val is not None:
					dim_filters[fn] = val

			source_rate, _matched = get_last_sle_rate(
				row.item_variant, warehouse=row.warehouse, **dim_filters
			)
			source_rate = flt(source_rate)

			outbound = {**base, "warehouse": row.warehouse, "qty": -qty, "rate": 0, "outgoing_rate": source_rate}
			inbound_base = dict(base)
			inbound_base["received_type"] = target_rt
			inbound = {**inbound_base, "warehouse": row.warehouse, "qty": qty, "rate": source_rate}
			entries.append(outbound)
			entries.append(inbound)

		return entries


# ----------------------------------------------------------------------
# UI grouping helpers — flat child rows ↔ source-bin grouped JSON
# ----------------------------------------------------------------------
def _source_key(row_data):
	"""Stable per-source-bin key. Prefer ref_docname (GRN Item row name) since
	one GRN row maps to exactly one source bin; fall back to (item, warehouse,
	dims) for legacy data without ref_docname."""
	from yrp.stock.dimensions import get_dimension_fieldnames

	ref = row_data.get("ref_docname")
	if ref:
		return ("ref", ref)
	dims = tuple(row_data.get(fn) for fn in get_dimension_fieldnames())
	return ("bin", row_data.get("item_variant"), row_data.get("warehouse"), dims)


def _group_items_for_ui(rows):
	"""flat child rows → list of source-bin dicts each with a `splits` list."""
	from yrp.stock.dimensions import get_dimension_fieldnames

	dim_fields = get_dimension_fieldnames()
	by_source = {}
	order = []
	for row in rows:
		row_data = row.as_dict() if hasattr(row, "as_dict") else dict(row)
		key = _source_key(row_data)
		if key not in by_source:
			source = {
				"item_variant": row_data.get("item_variant"),
				"warehouse": row_data.get("warehouse"),
				"grn_qty": flt(row_data.get("grn_qty")),
				"ref_doctype": row_data.get("ref_doctype"),
				"ref_docname": row_data.get("ref_docname"),
				"source_received_type": row_data.get("received_type"),
				"splits": [],
			}
			for fn in dim_fields:
				source[fn] = row_data.get(fn)
			by_source[key] = source
			order.append(key)
		by_source[key]["splits"].append({
			"target_received_type": row_data.get("target_received_type"),
			"qty": flt(row_data.get("qty")),
			"received_date": row_data.get("received_date"),
			"comments": row_data.get("comments") or "",
		})
	sources = [by_source[k] for k in order]
	_attach_display_meta(sources)
	return sources


def _ungroup_items_from_ui(item_details):
	"""Source-bin grouped JSON → flat list of child-row dicts ready for append."""
	from yrp.stock.dimensions import get_dimension_fieldnames

	dim_fields = get_dimension_fieldnames()
	payload = item_details
	if isinstance(payload, str):
		try:
			payload = json.loads(payload)
		except Exception:
			frappe.throw(_("Item Details payload is not valid JSON."))
	if not isinstance(payload, list):
		frappe.throw(_("Item Details payload must be a list of source bins."))

	rows = []
	for source in payload:
		base = {
			"item_variant": source.get("item_variant"),
			"warehouse": source.get("warehouse"),
			"grn_qty": flt(source.get("grn_qty")),
			"ref_doctype": source.get("ref_doctype"),
			"ref_docname": source.get("ref_docname"),
		}
		for fn in dim_fields:
			if fn == "received_type":
				base[fn] = source.get("source_received_type") or source.get(fn)
			else:
				base[fn] = source.get(fn)

		splits = source.get("splits") or []
		if not splits:
			frappe.throw(
				_("Item {0} at {1}: at least one target split is required.").format(
					base["item_variant"], base["warehouse"]
				)
			)
		for split in splits:
			row = dict(base)
			row["target_received_type"] = split.get("target_received_type")
			row["qty"] = flt(split.get("qty"))
			row["received_date"] = split.get("received_date")
			row["comments"] = split.get("comments") or None
			rows.append(row)
	return rows


# ----------------------------------------------------------------------
# Whitelist API
# ----------------------------------------------------------------------
@frappe.whitelist()
def get_initial_payload(against, against_id):
	"""Build the source-bin grouped payload for a fresh Inspection Entry from a
	submitted source document. Each source row becomes one source bin with a
	single default split (target = source, qty = source qty)."""
	if against == "Goods Received Note":
		return _grn_initial_payload(against_id)
	if against == "Stock Entry":
		return _stock_entry_initial_payload(against_id)
	frappe.throw(_("Inspection Entry against {0} is not supported yet.").format(against))


def _grn_initial_payload(grn):
	grn_doc = frappe.get_doc("Goods Received Note", grn)
	if grn_doc.docstatus != 1:
		frappe.throw(_("Goods Received Note {0} must be submitted.").format(grn))

	from yrp.stock.dimensions import get_dimension_fieldnames

	dim_fields = get_dimension_fieldnames()
	posting_date = str(grn_doc.posting_date) if grn_doc.posting_date else None

	sources = []
	for item in grn_doc.items:
		qty = flt(item.quantity)
		if qty <= 0.0001:
			continue
		source_rt = item.get("received_type")
		source = {
			"item_variant": item.item_variant,
			"warehouse": grn_doc.to_warehouse,
			"grn_qty": qty,
			"ref_doctype": "Goods Received Note Item",
			"ref_docname": item.name,
			"source_received_type": source_rt,
			"splits": [
				{
					"target_received_type": source_rt,
					"qty": qty,
					"received_date": posting_date,
					"comments": "",
				}
			],
		}
		for fn in dim_fields:
			if item.meta.get_field(fn):
				source[fn] = item.get(fn)
		sources.append(source)
	_attach_display_meta(sources)
	return sources


def _stock_entry_initial_payload(ste):
	ste_doc = frappe.get_doc("Stock Entry", ste)
	if ste_doc.docstatus != 1:
		frappe.throw(_("Stock Entry {0} must be submitted.").format(ste))
	if (ste_doc.purpose or "") != "Material Receipt":
		frappe.throw(
			_("Stock Entry {0} must have Purpose = 'Material Receipt' to be inspected.").format(ste)
		)

	from yrp.stock.dimensions import get_dimension_fieldnames

	dim_fields = get_dimension_fieldnames()
	posting_date = str(ste_doc.posting_date) if ste_doc.posting_date else None
	default_warehouse = ste_doc.to_warehouse

	sources = []
	for row in ste_doc.items:
		qty = flt(row.qty)
		if qty <= 0.0001:
			continue
		source_rt = row.get("received_type")
		source = {
			"item_variant": row.item,  # Stock Entry Detail uses `item`, not `item_variant`
			"warehouse": default_warehouse,
			"grn_qty": qty,
			"ref_doctype": "Stock Entry Detail",
			"ref_docname": row.name,
			"source_received_type": source_rt,
			"splits": [
				{
					"target_received_type": source_rt,
					"qty": qty,
					"received_date": posting_date,
					"comments": "",
				}
			],
		}
		for fn in dim_fields:
			if row.meta.get_field(fn):
				source[fn] = row.get(fn)
		sources.append(source)
	_attach_display_meta(sources)
	return sources


# ----------------------------------------------------------------------
# Display-meta enrichment (for the GRN-style pivot in the Vue editor)
# ----------------------------------------------------------------------
def _attach_display_meta(sources):
	"""Add `display_meta` to each source bin so the Vue editor can render the
	GRN-style pivot: one *block* per (parent Item · warehouse · non-RT
	stock-dimension values · non-primary attributes), rows by Source RT,
	columns by the parent Item's primary attribute (e.g. Size).

	IMPORTANT: stock-dimension fields (e.g. `lot`) are read generically via
	`get_stock_dimensions()` — never hardcoded by fieldname. Their labels come
	from the YRP Stock Settings configuration so the editor stays valid if a
	site adds/renames dimensions.
	"""
	from yrp.stock.dimensions import get_stock_dimensions
	from yrp.yrp.doctype.item.item import get_attribute_details

	dims = get_stock_dimensions()  # [{fieldname, label, ...}, ...]
	attr_cache = {}
	variant_cache = {}

	def _get_variant(name):
		if name not in variant_cache:
			variant_cache[name] = frappe.get_cached_doc("Item Variant", name)
		return variant_cache[name]

	def _get_attr(parent):
		if parent not in attr_cache:
			attr_cache[parent] = get_attribute_details(parent)
		return attr_cache[parent]

	for s in sources:
		iv = s.get("item_variant")
		if not iv:
			continue
		try:
			variant_doc = _get_variant(iv)
		except frappe.DoesNotExistError:
			continue
		parent_item = variant_doc.item
		if not parent_item:
			continue
		attr_details = _get_attr(parent_item)

		primary = attr_details.get("primary_attribute") or ""
		primary_values = list(attr_details.get("primary_attribute_values") or [])
		non_primary_names = list(attr_details.get("attributes") or [])

		variant_attrs = {
			row.attribute: row.attribute_value
			for row in (variant_doc.attributes or [])
		}
		primary_value = variant_attrs.get(primary, "") if primary else ""
		non_primary_attrs = {a: variant_attrs.get(a, "") for a in non_primary_names}

		# Group key — same key ⇒ same block in the UI.
		group_parts = [parent_item, str(s.get("warehouse") or "")]
		for d in dims:
			if d["fieldname"] == "received_type":
				continue
			group_parts.append(f"{d['fieldname']}={s.get(d['fieldname']) or ''}")
		for a in sorted(non_primary_names):
			group_parts.append(f"{a}={non_primary_attrs.get(a, '')}")
		group_key = "|".join(group_parts)

		# Human label — iterate dim metadata so any new dim (`lot`, `colour`,
		# ...) shows up automatically using its configured label.
		label_parts = [parent_item]
		for d in dims:
			fn = d["fieldname"]
			if fn == "received_type":
				continue
			val = s.get(fn)
			if val:
				label_parts.append(f"{d.get('label') or fn}: {val}")
		for a in non_primary_names:
			v = non_primary_attrs.get(a, "")
			if v:
				label_parts.append(v)
		group_label = " · ".join(label_parts)

		s["display_meta"] = {
			"parent_item": parent_item,
			"group_key": group_key,
			"group_label": group_label,
			"primary_attribute": primary,
			"primary_attribute_values": primary_values,
			"primary_attribute_value": primary_value,
			"non_primary_attributes": non_primary_attrs,
		}
	return sources


@frappe.whitelist()
def get_received_types():
	"""Dropdown source for the Vue editor."""
	return [
		r["name"]
		for r in frappe.get_all("Received Type", fields=["name"], order_by="name asc")
	]


# ----------------------------------------------------------------------
# Convert Stock — approver-gated SLE generation, separated from submit.
# ----------------------------------------------------------------------
def _approver_role():
	role = frappe.db.get_single_value("YRP Settings", "inspection_entry_approver_role")
	return (role or "").strip()


@frappe.whitelist()
def can_convert_stock(name):
	"""Return whether the current user may convert stock for this IE, plus the
	list of sibling IEs against the same `against_id` so the confirmation dialog
	can surface "these IEs have already converted stock".

	Response shape:
	  {
	    "can_convert": bool,
	    "reason": str,                # explains why not, if can_convert is False
	    "siblings": [
	      {"name": ..., "status": ..., "is_converted": 0|1, "posting_date": ...},
	      ...
	    ]
	  }
	"""
	role = _approver_role()
	doc = frappe.get_doc("Inspection Entry", name)

	# Only show siblings whose stock has actually been converted — drafts and
	# submitted-but-not-converted IEs are noise for this dialog.
	siblings = []
	if doc.against and doc.against_id:
		siblings = frappe.get_all(
			"Inspection Entry",
			filters={
				"against": doc.against,
				"against_id": doc.against_id,
				"name": ["!=", doc.name],
				"docstatus": 1,
			},
			or_filters={
				"is_converted": 1,
				"status": "Converted",
			},
			fields=["name", "status", "is_converted", "posting_date"],
			order_by="posting_date asc, name asc",
		)

	can_convert = True
	reason = ""
	if doc.docstatus != 1:
		can_convert, reason = False, _("Inspection Entry is not submitted.")
	elif doc.get("is_converted"):
		can_convert, reason = False, _("Stock has already been converted for this Inspection Entry.")
	elif (doc.status or "") == "Cancelled":
		can_convert, reason = False, _("Inspection Entry is cancelled.")
	elif not role:
		can_convert, reason = False, _("Approver role is not configured in YRP Settings.")
	elif role not in (frappe.get_roles(frappe.session.user) or []):
		can_convert, reason = False, _("You do not hold the {0} role.").format(role)

	return {
		"can_convert": can_convert,
		"reason": reason,
		"siblings": siblings,
	}


@frappe.whitelist()
def convert_stock(name):
	"""Write SLEs for this Inspection Entry. Only callable by users holding the
	role configured at `YRP Settings.inspection_entry_approver_role`.

	Idempotency: refuses to run if the IE is already Converted or Cancelled, or
	is not submitted.
	"""
	role = _approver_role()
	if not role:
		frappe.throw(
			_(
				"YRP Settings → Inspection Entry Approver Role is not configured. "
				"Stock conversion is disabled until an approver role is set."
			)
		)
	if role not in (frappe.get_roles(frappe.session.user) or []):
		frappe.throw(
			_("You need the {0} role to convert Inspection Entry stock.").format(role),
			frappe.PermissionError,
		)

	doc = frappe.get_doc("Inspection Entry", name)
	if doc.docstatus != 1:
		frappe.throw(_("Inspection Entry {0} must be submitted before converting stock.").format(name))
	current_status = doc.status or ""
	if current_status == "Converted":
		frappe.throw(_("Inspection Entry {0} has already converted stock.").format(name))
	if current_status == "Cancelled":
		frappe.throw(_("Inspection Entry {0} is cancelled and cannot convert stock.").format(name))

	from yrp.stock.stock_ledger import enqueue_voucher_repost, make_sl_entries

	entries = doc._build_sl_entries(cancel=False)
	if entries:
		make_sl_entries(entries)
	enqueue_voucher_repost(doc)
	doc.db_set("status", "Converted")
	doc.db_set("is_converted", 1)
	return {"status": "Converted", "is_converted": 1}
