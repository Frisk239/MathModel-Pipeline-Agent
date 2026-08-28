<script setup lang="ts">
import type { OutputItem } from "@/utils/response";
import { ChevronDown, TerminalSquare } from "lucide-vue-next";
import { computed, ref } from "vue";

// ---- Props ----

interface ToolRowProps {
	/** 代码执行工具行的两半：input 拍（代码）与 output 拍（结果） */
	code?: string | null;
	output?: OutputItem[] | null;
}

const props = defineProps<ToolRowProps>();

// ---- 状态推导 ----

type RowState = "running" | "ok" | "error";

const state = computed<RowState>(() => {
	if (!props.output) return "running";
	return props.output.some((o) => o.res_type === "error") ? "error" : "ok";
});

const stateMeta: Record<RowState, { dotClass: string; label: string }> = {
	running: { dotClass: "bg-blue-500 animate-pulse", label: "执行中" },
	ok: { dotClass: "bg-emerald-500", label: "完成" },
	error: { dotClass: "bg-red-500", label: "出错" },
};

// ---- 摘要与展开 ----

const expanded = ref(false);

const summary = computed(() => {
	if (!props.code) return "执行结果";
	const firstLine = props.code.trim().split("\n")[0] ?? "";
	return firstLine.length > 64 ? `${firstLine.slice(0, 64)}…` : firstLine;
});

function formatItem(o: OutputItem): string {
	if (o.res_type === "error") return `${o.name}: ${o.value}\n${o.traceback}`;
	return o.msg ?? "";
}

const outputText = computed(() =>
	(props.output ?? []).map(formatItem).join("\n").trim(),
);

const hasDetail = computed(() => Boolean(props.code) || outputText.value !== "");
</script>

<template>
  <div class="select-none">
    <!-- 单行：状态点 + 图标 + 摘要 + 展开箭头 -->
    <button type="button" class="flex w-full items-center gap-2 rounded-md px-1 py-1 text-left text-xs text-muted-foreground hover:bg-muted/50 transition-colors"
      :disabled="!hasDetail" @click="expanded = !expanded">
      <span :class="['h-1.5 w-1.5 shrink-0 rounded-full', stateMeta[state].dotClass]" aria-hidden="true" />
      <TerminalSquare class="h-3.5 w-3.5 shrink-0" />
      <span class="min-w-0 flex-1 truncate font-mono">{{ summary }}</span>
      <span class="shrink-0 text-[11px]">{{ stateMeta[state].label }}</span>
      <ChevronDown v-if="hasDetail" :class="['h-3.5 w-3.5 shrink-0 transition-transform', expanded ? 'rotate-180' : '']" />
    </button>
    <!-- IN/OUT 卡：等宽 12px 卡族 -->
    <div v-if="expanded" class="mt-1 flex flex-col gap-1.5 pl-6">
      <div v-if="props.code" class="rounded-md border bg-muted/40 p-2 font-mono text-[12px] leading-relaxed whitespace-pre-wrap break-all">
        {{ props.code }}
      </div>
      <div v-if="outputText" class="rounded-md border bg-muted/40 p-2 font-mono text-[12px] leading-relaxed whitespace-pre-wrap break-all"
        :class="state === 'error' ? 'text-red-600 dark:text-red-400' : ''">
        {{ outputText }}
      </div>
    </div>
  </div>
</template>
