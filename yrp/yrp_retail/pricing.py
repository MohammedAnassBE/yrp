"""Standard ERPNext selling-price policy for template-managed YRP Items.

The mixin surrounds ERPNext's actual tax calculation, after missing values and
pricing rules have been applied. Final validate hooks defend against later
controller/custom-hook changes. No legacy or purchase document is modified.
"""

import math

import frappe
from frappe import _

SALES_DOCTYPES = frozenset(("Sales Order", "Delivery Note", "Sales Invoice"))
EFFECTIVE_RATES = ("rate", "net_rate", "base_rate", "base_net_rate")
FREE_ZERO_FIELDS = (
	"price_list_rate", "base_price_list_rate", "rate_with_margin", "base_rate_with_margin",
	"margin_rate_or_amount", "discount_percentage", "discount_amount", "distributed_discount_amount",
	*EFFECTIVE_RATES, "amount", "base_amount", "net_amount", "base_net_amount",
)


def lock_pricing_policy(doc=None, method=None):
	"""Transaction-scoped mutex on a stable metadata row; never commits.

	Wire before_validate on Item, Item Price, Price List and the template, before
	other hooks acquire Item/template locks. Current reads below avoid stale
	REPEATABLE READ snapshots after a concurrent policy change has committed.
	"""
	frappe.db.sql("select name from `tabDocType` where name='YRP Item Master Template' for update")


def _current_rows(doctype, *, filters, fields=None, pluck=None):
	return frappe.db.get_values(
		doctype, filters=filters, fieldname=pluck or fields or ["name"],
		as_dict=not bool(pluck), pluck=bool(pluck), for_update=True, order_by="name",
	)


def check_rate(value, is_free, context):
	"""Reject non-finite rates and enforce exact zero or strictly positive rates."""
	try:
		rate = float(value)
	except (TypeError, ValueError, OverflowError):
		rate = float("nan")
	if not math.isfinite(rate) or (rate != 0 if is_free else rate <= 0):
		rule = _("exactly zero for a free Item") if is_free else _("greater than zero")
		frappe.throw(_("{0}: selling rate must be {1}.").format(context, rule))


def _managed_items(item_codes):
	lock_pricing_policy()
	codes = sorted({code for code in item_codes if code})
	if not codes:
		return {}
	return {
		row.name: bool(row.yrp_is_free_item)
		for row in _current_rows(
			"Item", filters={"name": ["in", codes], "yrp_item_master_template": ["is", "set"]},
			fields=["name", "yrp_is_free_item"],
		)
	}


def prepare_free_rows(doc, method=None):
	"""Clear free-item inputs immediately before ERPNext computes taxes/totals."""
	if doc.doctype not in SALES_DOCTYPES:
		return
	managed = _managed_items(row.item_code for row in doc.get("items", []))
	for row in doc.get("items", []):
		if managed.get(row.item_code):
			for field in FREE_ZERO_FIELDS:
				row.set(field, 0)
			row.set("margin_type", "")
			row.set("is_free_item", 1)


def validate_sales_document(doc, method=None):
	"""Check final effective rates, including document discounts and rounding.

	Return rows retain positive unit rates with negative quantities. Checking
	amount sign would incorrectly reject legitimate returns.
	"""
	if doc.doctype not in SALES_DOCTYPES:
		return
	managed = _managed_items(row.item_code for row in doc.get("items", []))
	for row in doc.get("items", []):
		if row.item_code not in managed:
			continue
		for field in EFFECTIVE_RATES:
			check_rate(row.get(field), managed[row.item_code], f"{row.item_code} ({field})")


class RetailSalesPricingMixin:
	"""Frappe extend_doctype_class mixin for SO, DN and SI only."""

	def calculate_taxes_and_totals(self):
		prepare_free_rows(self)
		result = super().calculate_taxes_and_totals()
		validate_sales_document(self)
		return result


def validate_item_price(doc, method=None):
	lock_pricing_policy()
	# Read the Price List master, never trust caller-supplied selling/buying flags.
	if not frappe.db.get_value("Price List", doc.price_list, "selling", for_update=True):
		return
	managed = _managed_items([doc.item_code])
	if doc.item_code in managed:
		check_rate(doc.price_list_rate, managed[doc.item_code], doc.item_code)


def _validate_existing_prices(item_codes, is_free):
	lock_pricing_policy()
	if not item_codes:
		return
	prices = _current_rows(
		"Item Price", filters={"item_code": ["in", item_codes]},
		fields=["name", "item_code", "price_list", "price_list_rate"],
	)
	lists = {row.price_list for row in prices}
	selling = set(_current_rows("Price List", filters={"name": ["in", sorted(lists)], "selling": 1}, pluck="name")) if lists else set()
	for price in prices:
		if price.price_list in selling:
			check_rate(price.price_list_rate, is_free, f"Item Price {price.name} ({price.item_code})")


def validate_item_free_flag(doc, method=None):
	"""Call after template inheritance; incompatible existing prices block save."""
	if doc.get("yrp_item_master_template"):
		_validate_existing_prices([doc.name], bool(doc.get("yrp_is_free_item")))


def validate_template_free_flag(doc, method=None):
	"""Validate all linked Items before changing their inherited free status."""
	if doc.is_new():
		return
	lock_pricing_policy()
	items = _current_rows("Item", filters={"yrp_item_master_template": doc.name}, pluck="name")
	_validate_existing_prices(items, bool(doc.get("is_free_item")))


def validate_price_list(doc, method=None):
	"""Prevent converting a buying list into a selling list with invalid prices."""
	if not doc.selling or doc.is_new():
		return
	lock_pricing_policy()
	prices = _current_rows("Item Price", filters={"price_list": doc.name}, fields=["name", "item_code", "price_list_rate"])
	managed = _managed_items(price.item_code for price in prices)
	for price in prices:
		if price.item_code in managed:
			check_rate(price.price_list_rate, managed[price.item_code], f"Item Price {price.name}")


class PricingLockMixin:
	"""Acquire policy mutex before Frappe locks an existing document for save."""
	def _save(self, *args, **kwargs):
		lock_pricing_policy()
		return super()._save(*args, **kwargs)
