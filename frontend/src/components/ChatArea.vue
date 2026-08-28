<script setup lang="ts">
import { useStickyScroll } from "@/composables/useStickyScroll";
import type { AgentType } from "@/utils/enum";
import type { InterpreterMessage, Message } from "@/utils/response";
import { ChevronDown, LoaderCircle } from "lucide-vue-next";
import { computed, ref } from "vue";
import Bubble from "./Bubble.vue";
import StreamingBubble from "./StreamingBubble.vue";
import SystemMessage from "./SystemMessage.vue";
import ToolRow from "./ToolRow.vue";

// ---- Props ----

const props = defineProps<{
	messages: Message[];
	streaming?: { agentType: AgentType; thinking: string; text: string } | null;
}>();

// ---- Scroll ----

const scrollRef = ref<HTMLDivElement | null>(null);

// 流式增量也触发粘底滚动（消息与 streaming 双数据源）
const scrollSource = computed(() => [
	props.messages,
	props.streaming?.thinking.length ?? 0,
	props.streaming?.text.length ?? 0,
]);
const { onScroll, isPinnedToBottom, scrollToBottom } = useStickyScroll(
	scrollRef,
	scrollSource,
);

// ---- Helpers ----

/** 消息时间戳（HH:mm），Agent 角色行展示用 */
function formatTime(message: Message): string | undefined {
	if (!message.created_at) return undefined;
	const ts = new Date(message.created_at);
	if (Number.isNaN(ts.getTime())) return undefined;
	return `${String(ts.getHours()).padStart(2, "0")}:${String(ts.getMinutes()).padStart(2, "0")}`;
}

// ---- execute_code 两拍配对 ----
// 后端分两条消息发：input 拍（code）先行、output 拍（results）随后。
// 时间线上配对成一行 ToolRow：output 拍填入最近的未闭合 input 拍；
// 无主 output 拍（如进程重启后残留）独立成"执行结果"行。

const codeExecPairs = computed(() => {
	const outputOf = new Map<string, InterpreterMessage>();
	const absorbed = new Set<string>();
	let openInput: InterpreterMessage | null = null;
	for (const raw of props.messages) {
		const msg = raw as InterpreterMessage;
		if (raw.msg_type !== "tool" || msg.tool_name !== "execute_code") continue;
		if (msg.input?.code != null) {
			openInput = msg;
		} else if (msg.output != null && openInput) {
			outputOf.set(openInput.id, msg);
			absorbed.add(msg.id);
			openInput = null;
		}
	}
	return { outputOf, absorbed };
});

function isCodeInput(msg: Message): msg is InterpreterMessage {
	const m = msg as InterpreterMessage;
	return (
		msg.msg_type === "tool" &&
		m.tool_name === "execute_code" &&
		m.input?.code != null
	);
}

function isOrphanCodeOutput(msg: Message): msg is InterpreterMessage {
	const m = msg as InterpreterMessage;
	return (
		msg.msg_type === "tool" &&
		m.tool_name === "execute_code" &&
		m.input?.code == null &&
		!codeExecPairs.value.absorbed.has(msg.id)
	);
}

function pairedOutput(msg: Message) {
	return codeExecPairs.value.outputOf.get(msg.id) ?? null;
}
</script>

<template>
  <div class="relative flex h-full flex-col p-3">
    <div ref="scrollRef" class="flex-1 overflow-y-auto" @scroll="onScroll">
      <!-- 空状态：历史消息加载前 / 任务尚未产生消息 -->
      <div v-if="props.messages.length === 0" class="flex h-full items-center justify-center">
        <div class="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle class="h-3.5 w-3.5 animate-spin" />
          正在加载任务记录…
        </div>
      </div>
      <template v-for="message in props.messages" :key="message.id">
        <div class="mb-3">
          <!-- 用户消息 -->
          <Bubble v-if="message.msg_type === 'user'" type="user" :content="message.content || ''" />
          <!-- agent 消息（CoderAgent/WriterAgent，只显示 content） -->
          <Bubble v-else-if="message.msg_type === 'agent'" type="agent" :agentType="message.agent_type"
            :content="message.content || ''" :time="formatTime(message)" />
          <!-- 系统消息 -->
          <SystemMessage v-else-if="message.msg_type === 'system'" :content="message.content || ''"
            :type="message.type" />
          <!-- 代码执行（input 拍 + 配对的 output 拍，四态单行卡） -->
          <ToolRow v-else-if="isCodeInput(message)" :code="message.input?.code" :output="pairedOutput(message)?.output" />
          <!-- 游离的执行结果拍（无对应 input，如中断后残留） -->
          <ToolRow v-else-if="isOrphanCodeOutput(message)" :output="message.output" />
        </div>
      </template>
      <!-- 流式过程气泡（思维链 + 正文打字机），done 后由终稿消息接管 -->
      <StreamingBubble v-if="props.streaming" :key="props.streaming.agentType" :agent-type="props.streaming.agentType"
        :thinking="props.streaming.thinking" :text="props.streaming.text" />
    </div>
    <!-- 回底浮钮：用户上翻离开底部时出现 -->
    <button v-show="!isPinnedToBottom && props.messages.length > 0" type="button"
      class="absolute bottom-5 right-5 flex h-9 w-9 items-center justify-center rounded-full border bg-background text-muted-foreground shadow-md transition-opacity hover:text-foreground"
      aria-label="回到底部" @click="scrollToBottom">
      <ChevronDown class="h-4 w-4" />
    </button>
  </div>
</template>

<style scoped>
/* 自定义滚动条样式 */
.overflow-y-auto::-webkit-scrollbar {
  width: 4px;
}

.overflow-y-auto::-webkit-scrollbar-track {
  @apply bg-transparent;
}

.overflow-y-auto::-webkit-scrollbar-thumb {
  @apply bg-gray-300 dark:bg-gray-600 rounded-full;
}

.overflow-y-auto::-webkit-scrollbar-thumb:hover {
  @apply bg-gray-400 dark:bg-gray-500;
}
</style>
