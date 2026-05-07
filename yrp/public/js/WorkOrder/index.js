import { createApp } from 'vue';
import WorkOrderItemEditor from './WorkOrderItemEditor.vue';

function _mount(wrapper, options) {
    const $wrapper = $(wrapper);
    $('<div class="item frappe-control">').appendTo($wrapper);
    const app = createApp(WorkOrderItemEditor, options || {});
    if (typeof SetVueGlobals === 'function') SetVueGlobals(app);
    const inst = app.mount($wrapper.get(0));
    return { app, inst };
}

export class WorkOrderItemEditorWrapper {
    constructor(wrapper, options) {
        const { app, inst } = _mount(wrapper, options);
        this.app = app;
        this.editor = inst;
    }
    get_items() { return this.editor.get_items(); }
    load_data(data) { this.editor.load_data(data); }
    update_status() { this.editor.update_status(); }
}
