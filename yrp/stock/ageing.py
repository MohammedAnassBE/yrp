"""Stock ageing — FIFO slot computation grouped by configured stock dimensions.

Uses yrp.stock.dimensions.get_dimension_fieldnames() instead of a hardcoded
lot dimension. ``FIFOSlots`` builds a per-(item, warehouse, *dims)
dictionary of FIFO entries that ageing reports / Stock Balance can consume.
"""

from typing import Dict, List, Tuple, Union

import frappe
from frappe.utils import date_diff, flt

from yrp.stock.dimensions import get_dimension_fieldnames


# ----------------------------------------------------------------------
# Stateless helpers
# ----------------------------------------------------------------------
def get_average_age(fifo_queue: list, to_date) -> float:
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


def get_range_age(fifo_queue: list, to_date, range1: int, range2: int, range3: int) -> tuple:
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

	def __init__(self, filters: dict | None = None, sle: list | None = None):
		self.filters = filters or {}
		self.sle = sle
		self.item_details: dict = {}
		self.transferred_item_details: dict = {}
		self.dim_fields = get_dimension_fieldnames()

	# Public ----------------------------------------------------------
	def generate(self) -> dict:
		if self.sle is None:
			# Stream the ledger: buffering every row costs more time and memory than the FIFO walk.
			frappe.get_cached_doc("System Settings")
			with frappe.db.unbuffered_cursor():
				self.__consume(order_within_second(self.__get_stock_ledger_entries()))
		else:
			self.__consume(self.sle)

		# Normalize completed dimension queues, not transactions: transfer tracking,
		# reconciliation and stock balances must retain their existing semantics.
		# Dust of either sign must not determine Earliest/Latest in any caller.
		for item in self.item_details.values():
			item["fifo_queue"] = [
				[qty, *slot[1:]]
				for slot in item["fifo_queue"]
				if (qty := flt(slot[0], 3, rounding_method="Banker's Rounding (legacy)"))
			]

		if not self.filters.get("show_warehouse_wise_stock"):
			self.item_details = self.__aggregate_by_item(self.item_details)

		return self.item_details

	# Internals -------------------------------------------------------
	def __consume(self, entries):
		for d in entries:
			key, fifo_queue, transfer_key = self.__init_key_stores(d)

			if d.voucher_type == "YRP Stock Reconciliation":
				prev = self.item_details[key].get("qty_after_transaction", 0)
				d.qty = flt(d.qty_after_transaction) - flt(prev)

			if d.qty > 0:
				self.__compute_incoming_stock(d, fifo_queue, transfer_key)
			else:
				self.__compute_outgoing_stock(d, fifo_queue, transfer_key)

			self.__update_balances(d, key)

	def __init_key_stores(self, row: dict) -> tuple:
		dim_key = tuple(row.get(fn) for fn in self.dim_fields)
		key = (row.item, row.warehouse, *dim_key)
		self.item_details.setdefault(key, {"details": row, "fifo_queue": []})
		fifo_queue = self.item_details[key]["fifo_queue"]
		transfer_key = (row.voucher_no, row.item, row.warehouse, *dim_key)
		self.transferred_item_details.setdefault(transfer_key, [])
		return key, fifo_queue, transfer_key

	def __compute_incoming_stock(self, row, fifo_queue: list, transfer_key: tuple):
		transfer_data = self.transferred_item_details.get(transfer_key)
		if transfer_data:
			self.__adjust_incoming_transfer(transfer_data, fifo_queue, row)
		else:
			if fifo_queue and flt(fifo_queue[0][0]) <= 0:
				fifo_queue[0][0] += flt(row.qty)
				fifo_queue[0][1] = row.posting_date
			else:
				fifo_queue.append([flt(row.qty), row.posting_date])

	def __compute_outgoing_stock(self, row, fifo_queue: list, transfer_key: tuple):
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

	def __adjust_incoming_transfer(self, transfer_data, fifo_queue: list, row):
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

	def __aggregate_by_item(self, wh_wise: dict) -> dict:
		agg: dict = {}
		for key, row in wh_wise.items():
			item = key[0]
			agg.setdefault(
				item,
				{"details": frappe._dict(), "fifo_queue": [], "qty_after_transaction": 0.0, "total_qty": 0.0},
			)
			agg[item]["details"].update(row["details"])
			agg[item]["fifo_queue"].extend(row["fifo_queue"])
			agg[item]["qty_after_transaction"] += flt(row["qty_after_transaction"])
			agg[item]["total_qty"] += flt(row["total_qty"])
		return agg

	def __get_stock_ledger_entries(self):
		sle = frappe.qb.DocType("YRP Stock Ledger Entry")
		query = (
			frappe.qb.from_(sle)
			.select(
				sle.item,
				sle.warehouse,
				sle.qty,
				sle.qty_after_transaction,
				sle.posting_date,
				sle.posting_time,
				sle.posting_datetime,
				sle.creation,
				sle.voucher_type,
				sle.voucher_no,
				*(sle[fieldname] for fieldname in self.dim_fields),
			)
			.where(sle.is_cancelled == 0)
			.orderby(sle.posting_datetime)
			.orderby(sle.creation)
		)
		if self.filters.get("to_date"):
			query = query.where(sle.posting_date <= self.filters["to_date"])
		warehouse = self.filters.get("warehouse")
		if isinstance(warehouse, (list, tuple)) and warehouse[0] == "in":
			query = query.where(sle.warehouse.isin(warehouse[1]))
		elif warehouse:
			query = query.where(sle.warehouse == warehouse)
		if self.filters.get("item"):
			query = query.where(sle.item == self.filters["item"])
		elif not warehouse and frappe.db.db_type == "mariadb":
			# Walking the ordered index beats a filesort of the whole ledger.
			query = query.force_index("posting_datetime_creation_index")
		return query.run(as_dict=True, as_iterator=True)


def order_within_second(rows):
	"""Restore posting_date, posting_time, creation order inside each posting second."""
	group, group_second = [], None
	for row in rows:
		# posting_datetime is stored either exact or truncated to the second.
		second = row.posting_datetime.replace(microsecond=0)
		if group and second != group_second:
			yield from sorted(group, key=ledger_sort_key)
			group = []
		group_second = second
		group.append(row)
	yield from sorted(group, key=ledger_sort_key)


def ledger_sort_key(row):
	return (row.posting_time, row.creation)
