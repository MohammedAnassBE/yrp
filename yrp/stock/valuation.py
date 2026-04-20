"""Stock valuation engine — FIFO and Moving Average.

Pure functions, no DB access, no dimensions. Ported from
production_api/mrp_stock/valuation.py with a Moving Average implementation
added.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import NewType

from frappe.utils import flt

StockBin = NewType("StockBin", list[float])  # [[qty, rate], ...]

QTY = 0
RATE = 1


def round_off_if_near_zero(number: float, precision: int = 7) -> float:
	if abs(0.0 - flt(number)) < (1.0 / (10**precision)):
		return 0.0
	return flt(number)


class BinWiseValuation(ABC):
	@abstractmethod
	def add_stock(self, qty: float, rate: float) -> None: ...

	@abstractmethod
	def remove_stock(
		self,
		qty: float,
		outgoing_rate: float = 0.0,
		rate_generator: Callable[[], float] | None = None,
	) -> list[StockBin]: ...

	@property
	@abstractmethod
	def state(self) -> list[StockBin]: ...

	def get_total_stock_and_value(self) -> tuple[float, float]:
		total_qty = 0.0
		total_value = 0.0
		for qty, rate in self.state:
			total_qty += flt(qty)
			total_value += flt(qty) * flt(rate)
		return round_off_if_near_zero(total_qty), round_off_if_near_zero(total_value)

	def __repr__(self):
		return str(self.state)

	def __iter__(self):
		return iter(self.state)

	def __eq__(self, other):
		if isinstance(other, list):
			return self.state == other
		return type(self) == type(other) and self.state == other.state


class FIFOValuation(BinWiseValuation):
	"""FIFO queue of [qty, rate] bins. Consumption is first-in-first-out."""

	__slots__ = ["queue"]

	def __init__(self, state: list[StockBin] | None):
		self.queue: list[StockBin] = state if state is not None else []

	@property
	def state(self) -> list[StockBin]:
		return self.queue

	def add_stock(self, qty: float, rate: float) -> None:
		"""Add incoming stock to the FIFO queue.

		If the last bin has the same rate, merge into it.
		Otherwise, append a new bin — unless the last bin has negative qty
		(from previous negative stock), in which case absorb the negative first.
		"""
		if not len(self.queue):
			self.queue.append([0, 0])

		if self.queue[-1][RATE] == rate:
			# Same rate as last bin — just add to it
			self.queue[-1][QTY] += qty
		else:
			if self.queue[-1][QTY] > 0:
				# Last bin has positive stock — append new bin at new rate
				self.queue.append([qty, rate])
			else:
				# Last bin has negative stock — absorb the negative first
				qty = self.queue[-1][QTY] + qty
				if qty > 0:
					self.queue[-1] = [qty, rate]
				else:
					self.queue[-1][QTY] = qty

	def remove_stock(
		self,
		qty: float,
		outgoing_rate: float = 0.0,
		rate_generator: Callable[[], float] | None = None,
	) -> list[StockBin]:
		"""Remove stock from the FIFO queue. Consumes oldest bins first.

		If outgoing_rate is specified, tries to find a bin with that exact rate first.
		Returns list of consumed [qty, rate] pairs for cost tracking.
		"""
		if not rate_generator:
			rate_generator = lambda: 0.0  # noqa: E731

		consumed_bins = []
		while qty:
			# If queue is empty, create an empty placeholder bin
			if not len(self.queue):
				self.queue.append([0, rate_generator()])

			# Find which bin to consume from
			# If outgoing_rate is specified, try to match it; otherwise use first bin (FIFO)
			index = None
			if outgoing_rate > 0:
				for idx, fifo_bin in enumerate(self.queue):
					if fifo_bin[RATE] == outgoing_rate:
						index = idx
						break
				if index is None:
					index = 0  # No matching rate found — fall back to FIFO order
			else:
				index = 0

			fifo_bin = self.queue[index]

			if qty >= fifo_bin[QTY]:
				# Case 1: Consume entire bin (requested qty >= bin qty)
				qty = round_off_if_near_zero(qty - fifo_bin[QTY])
				to_consume = self.queue.pop(index)
				consumed_bins.append(list(to_consume))

				if not self.queue and qty:
					# Case 1a: Queue exhausted but still qty remaining — negative stock
					self.queue.append([-qty, outgoing_rate or fifo_bin[RATE]])
					consumed_bins.append([qty, outgoing_rate or fifo_bin[RATE]])
					break
			else:
				# Case 2: Partial consumption (requested qty < bin qty)
				fifo_bin[QTY] = round_off_if_near_zero(fifo_bin[QTY] - qty)
				consumed_bins.append([qty, fifo_bin[RATE]])
				qty = 0

		return consumed_bins


class MovingAverageValuation(BinWiseValuation):
	"""Single-bin weighted-average valuation. State is always [[qty, avg_rate]]."""

	__slots__ = ["queue"]

	def __init__(self, state: list[StockBin] | None):
		self.queue: list[StockBin] = state if state else [[0.0, 0.0]]

	@property
	def state(self) -> list[StockBin]:
		return self.queue

	def add_stock(self, qty: float, rate: float) -> None:
		curr_qty, curr_rate = self.queue[0]
		new_qty = curr_qty + qty
		if new_qty > 0:
			new_rate = ((curr_qty * curr_rate) + (qty * rate)) / new_qty
		else:
			new_rate = rate
		self.queue[0] = [round_off_if_near_zero(new_qty), round_off_if_near_zero(new_rate)]

	def remove_stock(
		self,
		qty: float,
		outgoing_rate: float = 0.0,
		rate_generator: Callable[[], float] | None = None,
	) -> list[StockBin]:
		curr_qty, curr_rate = self.queue[0]
		new_qty = curr_qty - qty
		self.queue[0] = [round_off_if_near_zero(new_qty), curr_rate]
		return [[qty, curr_rate]]


def get_valuation_class(method: str = "FIFO"):
	return MovingAverageValuation if method == "Moving Average" else FIFOValuation
