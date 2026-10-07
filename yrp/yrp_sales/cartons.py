from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from yrp.yrp_retail.logic import require
from yrp.yrp_sales.invoicing import get_delivery_note_rows, lock_delivery_note

PACKING_SLIP = "YRP Packing Slip"
EPSILON = 1e-9


def get_packing_slip_workspace(delivery_note):
	"""Delivery Note rows and its non-cancelled Packing Slips with their rows, in carton order."""
	rows = frappe.get_all(
		"YRP Delivery Note Item",
		filters={"parent": delivery_note, "parenttype": "YRP Delivery Note"},
		fields=["name", "idx", "item_code", "item_name", "uom", "qty", "packed_qty"],
		order_by="idx",
	)
	slips = frappe.get_all(
		PACKING_SLIP,
		filters={"delivery_note": delivery_note, "docstatus": ["<", 2]},
		fields=["name", "carton_no", "docstatus", "status", "delivered_at"],
		order_by="carton_no, creation",
	)
	items = frappe.get_all(
		"YRP Packing Slip Item",
		filters={"parent": ["in", [slip.name for slip in slips] or [""]], "parenttype": PACKING_SLIP},
		fields=["parent", "dn_detail", "qty"],
		order_by="idx",
	)
	for slip in slips:
		slip["items"] = [row for row in items if row.parent == slip.name]
	return {"rows": rows, "packing_slips": slips}


def split_number_into_parts(number, parts):
	"""Even split of a whole `number`; the first parts take the remainder."""
	base, remainder = divmod(int(number), parts)
	split = [base + (1 if index < remainder else 0) for index in range(parts)]
	split[0] += flt(number) - int(number)
	return split


def split_into_cartons(lines):
	"""Cartons as lists of `{dn_detail, qty}` from lines spread evenly over their carton numbers.

	Each line is `{"carton_nos": [...], "rows": [{"dn_detail", "qty"}]}`: the rows' total is split with
	`split_number_into_parts`, then each carton's share is taken from the rows in order. Cartons left
	empty are dropped and the rest keep their number order."""
	cartons = defaultdict(lambda: defaultdict(float))
	for line in lines:
		numbers = line["carton_nos"]
		rows = [[row["dn_detail"], flt(row["qty"])] for row in line["rows"]]
		parts = split_number_into_parts(sum(qty for _, qty in rows), len(numbers))
		for number, share in zip(numbers, parts, strict=True):
			for row in rows:
				taken = min(share, row[1])
				if taken > EPSILON:
					cartons[number][row[0]] += taken
					row[1] -= taken
					share -= taken
	return [
		[{"dn_detail": detail, "qty": qty} for detail, qty in cartons[number].items()]
		for number in sorted(cartons)
		if cartons[number]
	]


def build_packing_slips(delivery_note, cartons):
	"""Replace the note's undelivered Packing Slips with one submitted slip per carton, numbered from 1.

	`cartons` is a list of cartons, each a list of `{dn_detail, qty}`; together they must hold every row's
	whole quantity. Callers check permissions."""
	cartons = frappe.parse_json(cartons) if isinstance(cartons, str) else cartons
	note = lock_delivery_note(delivery_note)
	require(note.docstatus == 1, _("Delivery Note {0} must be submitted.").format(delivery_note))
	require(not note.delivered_at, _("Delivery Note {0} is already delivered.").format(delivery_note))
	validate_cartons(delivery_note, cartons)
	cancel_packing_slips(delivery_note)
	names = []
	for number, carton in enumerate(cartons, 1):
		slip = frappe.get_doc(
			{
				"doctype": PACKING_SLIP,
				"delivery_note": delivery_note,
				"posting_date": nowdate(),
				"package_type": "Box",
				"package_count": 1,
				"carton_no": number,
				"items": [{"dn_detail": row["dn_detail"], "qty": flt(row["qty"])} for row in carton],
			}
		)
		slip.flags.ignore_permissions = True
		slip.insert()
		slip.submit()
		names.append(slip.name)
	return names


def validate_cartons(delivery_note, cartons):
	rows = get_delivery_note_rows(delivery_note)
	precision = frappe.get_precision("YRP Delivery Note Item", "qty")
	packed = defaultdict(float)
	for number, carton in enumerate(cartons, 1):
		require(carton, _("Carton {0} has no items.").format(number))
		for row in carton:
			require(
				row.get("dn_detail") in rows,
				_("Carton {0}: must reference a row of Delivery Note {1}.").format(number, delivery_note),
			)
			require(
				flt(row.get("qty")) > 0, _("Carton {0}: Quantity must be greater than zero.").format(number)
			)
			packed[row["dn_detail"]] += flt(row["qty"])
	for name, row in rows.items():
		require(
			flt(packed.get(name), precision) == flt(row.qty, precision),
			_("Move every packing-slip quantity into a carton before saving."),
		)


def cancel_packing_slips(delivery_note):
	"""Cancel submitted and delete draft Packing Slips; delivered ones cannot change."""
	slips = frappe.get_all(
		PACKING_SLIP,
		filters={"delivery_note": delivery_note, "docstatus": ["<", 2]},
		fields=["name", "docstatus", "delivered_at"],
		order_by="name",
	)
	delivered = [slip.name for slip in slips if slip.delivered_at]
	require(not delivered, _("Delivered Packing Slips {0} cannot be changed.").format(", ".join(delivered)))
	for row in slips:
		slip = frappe.get_doc(PACKING_SLIP, row.name)
		slip.flags.ignore_permissions = True
		if slip.docstatus == 1:
			slip.cancel()
		else:
			slip.delete()
