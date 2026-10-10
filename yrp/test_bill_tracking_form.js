const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const scriptPath = __dirname + "/yrp/doctype/yrp_bill_tracking/yrp_bill_tracking.js";
const PERMISSIONS_METHOD = "yrp.yrp.doctype.yrp_bill_tracking.yrp_bill_tracking.get_role_permissions";

function loadFormScript(permissions, roles = []) {
	let handlers;
	const xcalls = [];
	const context = {
		console,
		__: (value) => value,
		$: () => ({ html() {} }),
		frappe: {
			ui: { form: { on(_doctype, registeredHandlers) { handlers = registeredHandlers; } } },
			user: { has_role: (role) => roles.includes(role) },
			async xcall(method) {
				xcalls.push(method);
				return permissions;
			},
		},
	};
	vm.runInNewContext(fs.readFileSync(scriptPath, "utf8"), context, { filename: scriptPath });
	return { handlers, xcalls };
}

async function renderButtons(permissions, roles) {
	const { handlers, xcalls } = loadFormScript(permissions, roles);
	const buttons = [];
	const frm = {
		doc: { name: "YRP-BT-TEST", docstatus: 1, form_status: "Open" },
		fields_dict: { delivery_person_suggestion_html: { wrapper: null } },
		page: { btn_secondary: { hide() {} } },
		is_new: () => false,
		add_custom_button(label) { buttons.push(label); },
	};
	await handlers.refresh(frm);
	return { buttons, xcalls };
}

(async () => {
	{
		const { buttons, xcalls } = await renderButtons({ can_request_cancel: true });
		assert.deepEqual(xcalls, [PERMISSIONS_METHOD]);
		assert.deepEqual(buttons, ["Assign", "Cancel"]);
	}

	{
		const { buttons } = await renderButtons({ can_create_invoice: true });
		assert.deepEqual(buttons, ["Assign", "Create ERP Purchase Invoice"]);
	}

	{
		const { buttons } = await renderButtons({}, ["System Manager"]);
		assert.deepEqual(buttons, ["Assign", "Cancel"]);
	}

	{
		const { buttons } = await renderButtons({}, ["HR User", "Accounts Manager", "Accounts User"]);
		assert.deepEqual(buttons, ["Assign"]);
	}

	console.log("YRP Bill Tracking role setting tests passed");
})().catch((error) => {
	console.error(error);
	process.exitCode = 1;
});
