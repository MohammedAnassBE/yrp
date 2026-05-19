import { createApp } from 'vue';
import InspectionEntryEditor from './InspectionEntryEditor.vue';

function _mount(wrapper, options) {
    const $wrapper = $(wrapper);
    $('<div class="item frappe-control">').appendTo($wrapper);
    const app = createApp(InspectionEntryEditor, options || {});
    if (typeof SetVueGlobals === 'function') SetVueGlobals(app);
    const inst = app.mount($wrapper.get(0));
    return { app, inst };
}

export class InspectionEntryEditorWrapper {
    constructor(wrapper, options) {
        const { app, inst } = _mount(wrapper, options);
        this.app = app;
        this.editor = inst;
    }
    load_data(data) { this.editor.load_data(data); }
    get_items() { return this.editor.get_items(); }
    update_status() { this.editor.update_status(); }
}
