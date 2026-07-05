"""Stock ageing — FIFO slot computation grouped by configured stock dimensions.

Ported from production_api/mrp_stock/report/stock_ageing/stock_ageing.py and
refactored to use yrp.stock.dimensions.get_dimension_fieldnames() instead of a
hardcoded lot dimension. ``FIFOSlots`` builds a per-(item, warehouse, *dims)
dictionary of FIFO entries that ageing reports / Stock Balance can consume.
"""

from typing import Dict, List, Tuple, Union

import frappe
from frappe.utils import date_diff, flt

from yrp.stock.dimensions import get_dimension_fieldnames


# ----------------------------------------------------------------------
# Stateless helpers
# ----------------------------------------------------------------------
def get_average_age(fifo_queue: List, to_date) -> float:
	age_qty = total_qty = 0.0
	for batch in fifo_queue:
		batch_age = date_diff(to_date, batch[1])
		if isinstance(batch[0], (int, float)):
			age_qty += batch_age * batch[0]
			total_qty += batch[0]
		else:
			age_qty += batch_age
			total_qty += 1
	return flt(age_qty / total_qty, 2) if total_qty else 0.0


def get_range_age(fifo_queue: List, to_date, range1: int, range2: int, range3: int) -> Tuple:
	r1 = r2 = r3 = above = 0.0
	for entry in fifo_queue:
		age = date_diff(to_date, entry[1])
		qty = flt(entry[0])
		if age <= range1:
			r1 += qty
		elif age <= range2:
			r2 += qty
		elif age <= range3:
			r3 += qty
		else:
			above += qty
	return r1, r2, r3, above


# ----------------------------------------------------------------------
# FIFO slot builder
# ----------------------------------------------------------------------
class FIFOSlots:
	"""Build FIFO slots from SLE history, dimension-aware."""

	def __init__(self, filters: Dict | None = None, sle: List | None = None):
		self.filters = filters or {}
		self.sle = sle
		self.item_details: Dict = {}
		self.transferred_item_details: Dict = {}
		self.dim_fields = get_dimension_fieldnames()

	# Public ----------------------------------------------------------
	def generate(self) -> Dict:
		if self.sle is None:
			self.sle = self.__get_stock_ledger_entries()

		for d in self.sle:
			key, fifo_queue, transfer_key = self.__init_key_stores(d)

			if d.voucher_type == "Stock Reconciliation":
				prev = self.item_details[key].get("qty_after_transaction", 0)
				d.qty = flt(d.qty_after_transaction) - flt(prev)

			if d.qty > 0:
				self.__compute_incoming_stock(d, fifo_queue, transfer_key)
			else:
				self.__compute_outgoing_stock(d, fifo_queue, transfer_key)

			self.__update_balances(d, key)

		if not self.filters.get("show_warehouse_wise_stock"):
			self.item_details = self.__aggregate_by_item(self.item_details)

		return self.item_details

	# Internals -------------------------------------------------------
	def __init_key_stores(self, row: Dict) -> Tuple:
		dim_key = tuple(row.get(fn) for fn in self.dim_fields)
		key = (row.item, row.warehouse, *dim_key)
		self.item_details.setdefault(key, {"details": row, "fifo_queue": []})
		fifo_queue = self.item_details[key]["fifo_queue"]
		transfer_key = (row.voucher_no, row.item, row.warehouse, *dim_key)
		self.transferred_item_details.setdefault(transfer_key, [])
		return key, fifo_queue, transfer_key

	def __compute_incoming_stock(self, row, fifo_queue: List, transfer_key: Tuple):
		transfer_data = self.transferred_item_details.get(transfer_key)
		if transfer_data:
			self.__adjust_incoming_transfer(transfer_data, fifo_queue, row)
		else:
			if fifo_queue and flt(fifo_queue[0][0]) <= 0:
				fifo_queue[0][0] += flt(row.qty)
				fifo_queue[0][1] = row.posting_date
			else:
				fifo_queue.append([flt(row.qty), row.posting_date])

	def __compute_outgoing_stock(self, row, fifo_queue: List, transfer_key: Tuple):
		"""Remove qty from the FIFO queue for ageing calculation.

		Each FIFO slot is [qty, posting_date]. We consume from the front (oldest first).
		Consumed slots are saved in transferred_item_details for transfer tracking.
		"""
		qty_to_pop = abs(row.qty)
		while qty_to_pop:
			slot = fifo_queue[0] if fifo_queue else [0, None]

			if 0 < flt(slot[0]) <= qty_to_pop:
				# Case 1: Entire slot consumed — remove it from queue
				qty_to_pop -= flt(slot[0])
				self.transferred_item_details[transfer_key].append(fifo_queue.pop(0))

			elif not fifo_queue:
				# Case 2: Queue is empty but still have qty to consume — negative stock
				fifo_queue.append([-qty_to_pop, row.posting_date])
				self.transferred_item_details[transfer_key].append([qty_to_pop, row.posting_date])
				qty_to_pop = 0

			else:
				# Case 3: Partial consumption — reduce slot qty, keep it in queue
				slot[0] = flt(slot[0]) - qty_to_pop
				self.transferred_item_details[transfer_key].append([qty_to_pop, slot[1]])
				qty_to_pop = 0

	def __adjust_incoming_transfer(self, transfer_data, fifo_queue: List, row):
		remaining = flt(row.qty)

		def push(slot):
			if fifo_queue and flt(fifo_queue[0][0]) <= 0:
				fifo_queue[0][0] += flt(slot[0])
				fifo_queue[0][1] = slot[1]
			else:
				fifo_queue.append(slot)

		while remaining:
			if transfer_data and 0 < transfer_data[0][0] <= remaining:
				remaining -= transfer_data[0][0]
				push(transfer_data.pop(0))
			elif not transfer_data:
				push([remaining, row.posting_date])
				remaining = 0
			else:
				transfer_data[0][0] -= remaining
				push([remaining, transfer_data[0][1]])
				remaining = 0

	def __update_balances(self, row, key):
		self.item_details[key]["qty_after_transaction"] = row.qty_after_transaction
		self.item_details[key]["total_qty"] = self.item_details[key].get("total_qty", 0) + row.qty

	def __aggregate_by_item(self, wh_wise: Dict) -> Dict:
		agg: Dict = {}
		for key, row in wh_wise.items():
			item = key[0]
			agg.setdefault(item, {"details": frappe._dict(), "fifo_queue": [], "qty_after_transaction": 0.0, "total_qty": 0.0})
			agg[item]["details"].update(row["details"])
			agg[item]["fifo_queue"].extend(row["fifo_queue"])
			agg[item]["qty_after_transaction"] += flt(row["qty_after_transaction"])
			agg[item]["total_qty"] += flt(row["total_qty"])
		return agg

	def __get_stock_ledger_entries(self) -> List[Dict]:
		fields = [
			"item", "warehouse", "qty", "qty_after_transaction",
			"posting_date", "posting_time", "voucher_type", "voucher_no",
		] + self.dim_fields
		filters: Dict = {"is_cancelled": 0}
		if self.filters.get("to_date"):
			filters["posting_date"] = ["<=", self.filters["to_date"]]
		if self.filters.get("warehouse"):
			filters["warehouse"] = self.filters["warehouse"]
		if self.filters.get("item"):
			filters["item"] = self.filters["item"]
		return frappe.get_all(
			"Stock Ledger Entry",
			filters=filters,
			fields=fields,
			order_by="posting_date asc, posting_time asc, creation asc",
		)
