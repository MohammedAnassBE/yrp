"""Replace historical attribute-value text with scoped master Links."""

from yrp.attribute_links import backfill


def execute():
	backfill()
