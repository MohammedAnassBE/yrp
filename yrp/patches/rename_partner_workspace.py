"""One-time legacy route repair; normal workspace JSON sync owns future updates."""


def execute():
	from yrp.yrp_partner.workspace import setup_partner_workspace
	setup_partner_workspace()
