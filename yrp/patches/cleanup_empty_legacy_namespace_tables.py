from yrp.namespace_migration import drop_empty_legacy_namespace_tables


def execute():
	drop_empty_legacy_namespace_tables(("yrp",))
