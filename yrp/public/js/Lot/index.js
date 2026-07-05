import { createApp } from "vue";
import LotOrder from "./components/LotOrder.vue";
import OCRDetail from "./components/OCRDetail.vue";


function mount_vue_component(component, wrapper) {
	const app = createApp(component);
	if (typeof SetVueGlobals === "function") SetVueGlobals(app);
	return {
		app,
		vue: app.mount($(wrapper).get(0)),
	};
}


export class LotOrderWrapper {
	constructor(wrapper) {
		this.$wrapper = $(wrapper);
		this.make_app();
	}
	make_app() {
		const mounted = mount_vue_component(LotOrder, this.$wrapper);
		this.app = mounted.app;
		this.vue = mounted.vue;
	}
	get_data() {
		return JSON.parse(JSON.stringify(this.vue.list_item));
	}
	show_inputs() {
		this.vue.show_add_items();
	}
	load_data(item_details) {
		this.vue.load_data(JSON.parse(JSON.stringify(item_details)));
	}
}


export class OCRDetailWrapper {
	constructor(wrapper) {
		this.$wrapper = $(wrapper);
		this.make_app();
	}
	make_app() {
		const mounted = mount_vue_component(OCRDetail, this.$wrapper);
		this.app = mounted.app;
		this.vue = mounted.vue;
	}
}
