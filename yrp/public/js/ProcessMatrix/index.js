import { createApp } from 'vue';
import EditProcessMatrix from './EditProcessMatrix.vue';

export class EditProcessMatrixWrapper {
    constructor(wrapper) {
        this.$wrapper = $(wrapper);
        this.make_body();
    }

    make_body() {
        $('<div class="process-matrix-frappe-control">').appendTo(this.$wrapper);
        this.app = createApp(EditProcessMatrix);
        this.editor = this.app.mount(this.$wrapper.get(0));
    }

    load(payload) {
        this.editor.load(payload);
    }
}
