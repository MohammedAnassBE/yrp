<!-- @yrp/web-engine — CompositeTree (Track 1 item 1).

     Root renderer of a validated composite tree: enforces the node/depth caps
     (grammar.js) BEFORE the first node renders, then hands off to the
     recursive CompositeNode. Over-cap or shapeless trees render the honest
     muted fallback (counts for managers, console.warn always) — never a
     crash, never silent blankness. A genuine render throw is still caught by
     the host BlockBoundary (spec §7.1).

     Props are the HOST's wiring (the consumer `composite` block):
       tree       — the validated node tree from layout JSON
       scope      — host-fetched data the bindings may read (dot-paths only)
       dateFormat — the layout's dateFormat knob (feeds the `date` formatter)
       dark       — active scheme is dark (feeds registry status-chip colours)
     The engine itself fetches NOTHING here — data arrives from the
     permissioned host, per USE_CASE §3(c)/(d). -->
<script setup>
import { computed } from "vue"
import { getContext } from "../context.js"
import { COMPOSITE_MAX_DEPTH, COMPOSITE_MAX_NODES } from "./grammar.js"
import { treeStats } from "./binding.js"
import CompositeNode from "./CompositeNode.vue"

const props = defineProps({
	tree: { type: Object, default: null },
	scope: { type: Object, default: () => ({}) },
	dateFormat: { type: String, default: "" },
	dark: { type: Boolean, default: false },
})

const showDetail = getContext().isManager()

const ctx = computed(() => ({ dateFormat: props.dateFormat || undefined, dark: props.dark }))

const stats = computed(() =>
	props.tree && typeof props.tree === "object" && !Array.isArray(props.tree)
		? treeStats(props.tree)
		: { nodes: 0, depth: 0 }
)

const overCaps = computed(
	() => stats.value.nodes > COMPOSITE_MAX_NODES || stats.value.depth > COMPOSITE_MAX_DEPTH
)

const renderable = computed(
	() => stats.value.nodes > 0 && typeof props.tree?.type === "string" && !overCaps.value
)

if (props.tree && !renderable.value) {
	console.warn(
		overCaps.value
			? `[yrp-web] composite: tree exceeds caps (${stats.value.nodes} nodes / depth ${stats.value.depth}; max ${COMPOSITE_MAX_NODES}/${COMPOSITE_MAX_DEPTH}) — not rendering`
			: "[yrp-web] composite: tree has no valid root node — not rendering",
		props.tree
	)
}
</script>

<template>
	<div v-if="renderable" class="yrp-composite">
		<CompositeNode :node="tree" :scope="scope" :ctx="ctx" path="tree" :depth="1" />
	</div>
	<div v-else-if="tree" class="yrp-composite yrp-composite--invalid">
		<span>This widget's layout can't be rendered.</span>
		<code v-if="showDetail" class="yrp-composite__detail">
			{{
				overCaps
					? `tree exceeds caps: ${stats.nodes} nodes / depth ${stats.depth} (max ${COMPOSITE_MAX_NODES} / ${COMPOSITE_MAX_DEPTH})`
					: "tree has no valid root node (expected { type, props, children })"
			}}
		</code>
	</div>
	<!-- no tree at all → nothing renders; the BlockBoundary collapses it -->
</template>

<style scoped>
.yrp-composite {
	min-width: 0;
}
.yrp-composite--invalid {
	display: flex;
	flex-direction: column;
	gap: var(--space-1, 4px);
	padding: var(--space-4, 16px);
	border: 1px dashed var(--esd-line, #dfe8e4);
	border-radius: var(--radius, 12px);
	background: var(--esd-card, #ffffff);
	color: var(--esd-muted, #5f6e68);
	font-size: 13px;
}
.yrp-composite__detail {
	font-size: 12px;
	opacity: 0.8;
}
</style>
