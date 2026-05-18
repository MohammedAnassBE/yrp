import { createApp } from "vue";
import SuggestedBillDeliveryPerson from "./components/SuggestedBillDeliveryPerson.vue";

export class SuggestedBillDeliveryPersonWrapper {
	constructor(wrapper) {
		this.$wrapper = $(wrapper);
		this.makeBody();
	}

	makeBody() {
		this.app = createApp(SuggestedBillDeliveryPerson);
		if (typeof SetVueGlobals === "function") SetVueGlobals(this.app);
		this.vue = this.app.mount(this.$wrapper.get(0));
	}

	update_for_new_supplier(supplier) {
		this.vue.update_for_new_supplier(supplier);
	}
}
