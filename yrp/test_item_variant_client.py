import json
import subprocess
from pathlib import Path

from frappe.tests import UnitTestCase


class TestItemVariantClient(UnitTestCase):
	def test_variant_form_exposes_computed_rename_action(self):
		item_script = Path(__file__).parent / "public" / "js" / "item.js"
		harness = r"""
const itemScript = process.argv[1];
let itemHandlers;
const menuItems = [];
const calls = [];
let reloads = 0;
let renameEvent = null;

global.__ = (value) => value;
global.document = {};
global.locals = { Item: { "OLD-VARIANT": {} } };
global.$ = () => ({
	trigger: (event, args) => {
		renameEvent = { event, args };
	},
	empty: () => {},
});
global.frappe = {
	ui: {
		form: {
			on: (doctype, handlers) => {
				if (doctype === "Item") itemHandlers = handlers;
			},
		},
	},
	production: { ui: {} },
	xcall: (method, args) => {
		calls.push({ method, args });
		return Promise.resolve("ITEM-TEMPLATE-80 cm");
	},
	msgprint: () => {},
};

require(itemScript);

const frm = {
	doctype: "Item",
	doc: {
		name: "OLD-VARIANT",
		variant_of: "ITEM-TEMPLATE",
		has_variants: 0,
	},
	fields_dict: {},
	is_new: () => false,
	page: {
		add_menu_item: (label, action) => menuItems.push({ label, action }),
	},
	reload_doc: () => {
		reloads += 1;
	},
	toggle_display: () => {},
};

(async () => {
	itemHandlers.refresh(frm);
	const renameAction = menuItems.find((item) => item.label === "Rename Variant");
	if (renameAction) await renameAction.action();
	process.stdout.write(JSON.stringify({
		labels: menuItems.map((item) => item.label),
		calls,
		reloads,
		renameEvent,
	}));
})().catch((error) => {
	console.error(error);
	process.exit(1);
});
"""
		completed = subprocess.run(
			["node", "-e", harness, str(item_script)],
			check=True,
			capture_output=True,
			text=True,
		)
		result = json.loads(completed.stdout)

		self.assertIn("Rename Variant", result["labels"])
		self.assertEqual(
			result["calls"],
			[
				{
					"method": "yrp.yrp.doctype.yrp_item_variant.yrp_item_variant.rename_item_variant",
					"args": {
						"variant": "OLD-VARIANT",
						"freeze": True,
						"freeze_message": "Renaming...",
					},
				}
			],
		)
		self.assertEqual(result["reloads"], 1)
		self.assertEqual(
			result["renameEvent"],
			{
				"event": "rename",
				"args": ["Item", "OLD-VARIANT", "ITEM-TEMPLATE-80 cm"],
			},
		)
