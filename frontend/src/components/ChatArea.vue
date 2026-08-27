<script setup lang="ts">
import { useStickyScroll } from "@/composables/useStickyScroll";
import type { AgentType } from "@/utils/enum";
import type { Message } from "@/utils/response";
import { LoaderCircle } from "lucide-vue-next";
import { computed, ref } from "vue";
import Bubble from "./Bubble.vue";
import StreamingBubble from "./StreamingBubble.vue";
import SystemMessage from "./SystemMessage.vue";

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
const { onScroll } = useStickyScroll(scrollRef, scrollSource);

// ---- Helpers ----

/** 消息时间戳（HH:mm），Agent 角色行展示用 */
function formatTime(message: Message): string | undefined {
	if (!message.created_at) return undefined;
	const ts = new Date(message.created_at);
	if (Number.isNaN(ts.getTime())) return undefined;
	return `${String(ts.getHours()).padStart(2, "0")}:${String(ts.getMinutes()).padStart(2, "0")}`;
}
</script>

<template>
  <div class="flex h-full flex-col p-3">
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
        </div>
      </template>
      <!-- 流式过程气泡（思维链 + 正文打字机），done 后由终稿消息接管 -->
      <StreamingBubble v-if="props.streaming" :key="props.streaming.agentType" :agent-type="props.streaming.agentType"
        :thinking="props.streaming.thinking" :text="props.streaming.text" />
    </div>
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
