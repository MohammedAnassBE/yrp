"""Permission-aware, transaction-UOM reporting."""
from yrp.yrp_retail.reporting import run

def execute(filters=None):
	return run('fulfilment', filters)
