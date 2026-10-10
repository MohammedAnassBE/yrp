const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const scriptPath = __dirname + "/public/js/purchase_order_list.js";
const erpnextListScriptPath =
	__dirname + "/../../erpnext/erpnext/buying/doctype/purchase_order/purchase_order_list.js";
const frappeIndicatorScriptPath =
	__dirname + "/../../frappe/frappe/public/js/frappe/model/indicator.js";

function loadSettings() {
	const nativeIndicator = (doc) => ["ERPNext", "orange", `status,=,${doc.status}`];
	const settings = {
		add_fields: ["status", "per_received", "per_billed"],
		get_indicator: nativeIndicator,
	};
	const context = {
		__: (value) => value,
		frappe: {
			listview_settings: { "Purchase Order": settings },
		},
	};
	const source = fs.existsSync(scriptPath) ? fs.readFileSync(scriptPath, "utf8") : "";
	vm.runInNewContext(source, context, { filename: scriptPath });
	return { settings, nativeIndicator };
}

function loadFrappeIndicator() {
	const context = {
		console,
		locals: { "Workflow State": {} },
		__: (value) => value,
		flt: (value) => Number(value || 0),
		frappe: {
			listview_settings: {},
			model: { is_submittable: () => true },
			workflow: {
				workflows: {},
				avoid_status_override: {},
				get_state_fieldname: () => null,
			},
			get_meta: () => ({ states: [] }),
			meta: { has_field: () => false },
			scrub: (value) => value,
			utils: { guess_colour: () => "gray" },
		},
	};
	vm.createContext(context);
	for (const path of [frappeIndicatorScriptPath, erpnextListScriptPath, scriptPath]) {
		vm.runInContext(fs.readFileSync(path, "utf8"), context, { filename: path });
	}
	return context;
}

{
	const { settings } = loadSettings();
	assert.ok(settings.add_fields.includes("is_yrp_managed"));
	assert.ok(settings.add_fields.includes("yrp_fulfillment_status"));
	const cases = [
		["Draft", "orange"],
		["Ordered", "blue"],
		["Partially Received", "yellow"],
		["Received", "green"],
		["Partially Cancelled", "grey"],
		["Cancelled", "darkgrey"],
		["Closed", "light-blue"],
	];
	for (const [status, color] of cases) {
		assert.deepEqual(
			Array.from(settings.get_indicator({
				is_yrp_managed: 1,
				yrp_fulfillment_status: status,
				status: "To Receive",
				per_received: 0,
				per_billed: 0,
			})),
			[
				status,
				color,
				`is_yrp_managed,=,1|yrp_fulfillment_status,=,${status}`,
			],
		);
	}
}

{
	const { settings, nativeIndicator } = loadSettings();
	const ordinaryPurchaseOrder = {
		is_yrp_managed: 0,
		status: "To Receive and Bill",
		per_received: 0,
		per_billed: 0,
	};
	assert.deepEqual(
		settings.get_indicator(ordinaryPurchaseOrder),
		nativeIndicator(ordinaryPurchaseOrder),
	);
}

{
	const context = loadFrappeIndicator();
	const getIndicator = (doc) =>
		Array.from(context.frappe.get_indicator(doc, "Purchase Order"));
	const managedCases = [
		[0, "Draft", "orange"],
		[1, "Ordered", "blue"],
		[1, "Partially Received", "yellow"],
		[1, "Received", "green"],
		[1, "Partially Cancelled", "grey"],
		[2, "Cancelled", "darkgrey"],
		[1, "Closed", "light-blue"],
	];
	for (const [docstatus, status, color] of managedCases) {
		assert.deepEqual(
			getIndicator({
				doctype: "Purchase Order",
				docstatus,
				is_yrp_managed: 1,
				yrp_fulfillment_status: status,
				status: "To Receive",
			}),
			[
				status,
				color,
				`is_yrp_managed,=,1|yrp_fulfillment_status,=,${status}`,
			],
		);
	}

	const ordinaryCases = [
		[{ docstatus: 0, status: "Draft" }, ["Draft", "red", "docstatus,=,0"]],
		[{ docstatus: 2, status: "Cancelled" }, ["Cancelled", "red", "docstatus,=,2"]],
		[{ docstatus: 1, status: "Closed" }, ["Closed", "green", "status,=,Closed"]],
		[
			{ docstatus: 1, status: "To Receive", advance_payment_status: "Initiated" },
			["To Pay", "gray", "advance_payment_status,=,Initiated"],
		],
		[
			{ docstatus: 1, status: "To Receive", per_received: 0, per_billed: 0 },
			[
				"To Receive and Bill",
				"orange",
				"per_received,<,100|per_billed,<,100|status,!=,Closed|docstatus,=,1",
			],
		],
		[
			{ docstatus: 1, status: "To Receive", per_received: 0, per_billed: 100 },
			[
				"To Receive",
				"orange",
				"per_received,<,100|per_billed,=,100|status,!=,Closed|docstatus,=,1",
			],
		],
		[
			{ docstatus: 1, status: "To Bill", per_received: 100, per_billed: 0 },
			[
				"To Bill",
				"orange",
				"per_received,=,100|per_billed,<,100|status,!=,Closed|docstatus,=,1",
			],
		],
		[
			{ docstatus: 1, status: "Completed", per_received: 100, per_billed: 100 },
			[
				"Completed",
				"green",
				"per_received,=,100|per_billed,=,100|status,!=,Closed|docstatus,=,1",
			],
		],
	];
	for (const [doc, expected] of ordinaryCases) {
		assert.deepEqual(
			getIndicator({ doctype: "Purchase Order", is_yrp_managed: 0, ...doc }),
			expected,
		);
	}
}

console.log("YRP Purchase Order list indicator tests passed");
