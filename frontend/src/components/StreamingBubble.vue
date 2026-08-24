<script setup lang="ts">
import type { AgentType } from "@/utils/enum";
import { Brain, ChevronDown, LoaderCircle } from "lucide-vue-next";
import { marked } from "marked";
import { computed, ref, watch } from "vue";

// 流式过程气泡：思维链折叠行（运行中滚动显示最后一行 + shimmer 扫光）
// 与正文打字机。形态参考 deepseek-harness 的 ReasoningRow：默认收起、
// 轻量纯文本（不做 markdown），结束后由终稿 agent 消息接管。

const props = defineProps<{
	agentType: AgentType;
	thinking: string;
	text: string;
}>();

const expanded = ref(false);
const thinkBodyRef = ref<HTMLPreElement | null>(null);
const summaryRef = ref<HTMLDivElement | null>(null);

const lastLine = computed(() => {
	const content = props.thinking.trim();
	if (!content) return "";
	const lines = content.split("\n").filter(Boolean);
	return lines[lines.length - 1] ?? "";
});

/** 正文富文本渲染（与终稿 Bubble 同一渲染器，流式期间高频重解析量级可忽略） */
const renderedText = computed(() => marked.parse(props.text));

/** 正文气泡头像（与 Bubble 的 agent 头像一致） */
const agentEmoji = computed(() => {
	switch (props.agentType) {
		case "CoderAgent":
			return "👨‍💻";
		case "WriterAgent":
			return "✍️";
		default:
			return "🤖";
	}
});

watch(
	() => [props.thinking, props.text],
	() => {
		requestAnimationFrame(() => {
			if (summaryRef.value) {
				summaryRef.value.scrollLeft = summaryRef.value.scrollWidth;
			}
			const body = thinkBodyRef.value;
			if (expanded.value && body) {
				// 仅当用户本来就停在展开体底部时才跟随增量；
				// 用户上滑回看思考过程时不拉回
				const atBottom =
					body.scrollHeight - body.scrollTop - body.clientHeight <= 40;
				if (atBottom) body.scrollTop = body.scrollHeight;
			}
		});
	},
);
</script>

<template>
  <div class="mb-3 flex flex-col gap-2">
    <!-- 思维链折叠行 -->
    <div v-if="props.thinking" class="overflow-hidden rounded-lg border bg-muted/40 text-xs">
      <button type="button" class="streaming-think-header flex w-full items-center gap-1.5 px-2.5 py-1.5"
        @click="expanded = !expanded">
        <Brain class="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span class="shrink-0 font-medium text-muted-foreground">思考中</span>
        <div ref="summaryRef" class="think-summary min-w-0 flex-1 overflow-hidden text-left">
          <span class="block truncate text-muted-foreground/80">{{ lastLine }}</span>
        </div>
        <ChevronDown class="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform"
          :class="{ 'rotate-180': expanded }" />
      </button>
      <pre v-show="expanded" ref="thinkBodyRef"
        class="think-body max-h-64 overflow-y-auto whitespace-pre-wrap break-words px-3 pb-2 text-muted-foreground">{{ props.thinking }}</pre>
    </div>
    <!-- 正文打字机（markdown 富文本渲染） -->
    <div v-if="props.text" class="flex flex-col gap-1">
      <span class="text-2xl select-none mb-1">{{ agentEmoji }}</span>
      <div class="relative max-w-[80%] rounded-2xl bg-muted px-4 py-2 text-sm">
        <div class="prose prose-sm prose-slate max-w-none" v-html="renderedText"></div>
        <span class="streaming-cursor absolute right-2 bottom-2" />
      </div>
    </div>
    <!-- 还没有任何增量：等待模型首 token -->
    <div v-if="!props.text && !props.thinking" class="flex items-center gap-1.5 text-xs text-muted-foreground">
      <LoaderCircle class="h-3 w-3 animate-spin" />
      <span>{{ props.agentType }} 正在思考…</span>
    </div>
  </div>
</template>

<style scoped>
/* 运行中折叠行的 shimmer 扫光（prefers-reduced-motion 时关闭） */
.streaming-think-header {
	position: relative;
	overflow: hidden;
}

.streaming-think-header::after {
	content: "";
	position: absolute;
	inset: 0;
	background: linear-gradient(
		100deg,
		transparent 30%,
		hsl(var(--muted-foreground) / 0.08) 50%,
		transparent 70%
	);
	transform: translateX(-100%);
	animation: think-shimmer 2.6s infinite;
	pointer-events: none;
}

@keyframes think-shimmer {
	to {
		transform: translateX(100%);
	}
}

.think-summary span {
	display: inline-block;
	min-width: 100%;
}

.think-body::-webkit-scrollbar {
	width: 4px;
}

@media (prefers-reduced-motion: reduce) {
	.streaming-think-header::after {
		animation: none;
	}
}

/* 打字机光标 */
.streaming-cursor {
	display: inline-block;
	width: 2px;
	height: 1em;
	margin-left: 2px;
	vertical-align: text-bottom;
	background: hsl(var(--foreground) / 0.7);
	animation: cursor-blink 1s step-end infinite;
}

@keyframes cursor-blink {
	50% {
		opacity: 0;
	}
}
</style>
