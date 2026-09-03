from yrp.namespace_migration import reconcile_legacy_single_child_parents
from yrp.stock.dimensions import clear_dimension_cache


def execute():
	reconcile_legacy_single_child_parents(("yrp",))
	clear_dimension_cache()
