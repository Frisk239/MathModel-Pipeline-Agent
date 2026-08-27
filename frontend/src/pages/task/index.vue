<script setup lang="ts">
import { getWriterSeque } from "@/apis/commonApi";
import CoderEditor from "@/components/AgentEditor/CoderEditor.vue";
import ModelerEditor from "@/components/AgentEditor/ModelerEditor.vue";
import WriterEditor from "@/components/AgentEditor/WriterEditor.vue";
import ChatArea from "@/components/ChatArea.vue";
import { Button } from "@/components/ui/button";
import {
	ResizableHandle,
	ResizablePanel,
	ResizablePanelGroup,
} from "@/components/ui/resizable";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import FilesSheet from "@/pages/task/components/FileSheet.vue";
import { useTaskStore } from "@/stores/task";
import { agentMetaOf } from "@/utils/agentMeta";
import { LoaderCircle } from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

// ---- Props ----

const props = defineProps<{ task_id: string }>();

// ---- Reactive State ----

const taskStore = useTaskStore();

/** 论文写作顺序 */
const writerSequence = ref<string[]>([]);

let timer: ReturnType<typeof setInterval> | null = null;
const now = ref<number>(Date.now());

/** 任务真实起始时间：首条消息的 created_at（比页面挂载更接近任务起点，
 *  刷新页面不归零；无历史消息时回退页面挂载时间） */
const taskStart = computed<number>(() => {
	const first = taskStore.chatMessages.find((m) => m.created_at);
	if (first?.created_at) {
		const ts = new Date(first.created_at).getTime();
		if (!Number.isNaN(ts)) return ts;
	}
	return now.value;
});

/** 任务终点：运行中取当前时间；已结束取最后一条消息时间（历史任务的
 *  真实时长，而非「开始到我打开页面」） */
const taskEnd = computed<number>(() => {
	if (taskStore.isRunning) return now.value;
	const list = taskStore.chatMessages;
	for (let i = list.length - 1; i >= 0; i--) {
		if (list[i].created_at) {
			const ts = new Date(list[i].created_at as string).getTime();
			if (!Number.isNaN(ts)) return ts;
		}
	}
	return now.value;
});

/** 运行时长：isRunning 期间每秒推进；结束后冻结在终值 */
const runningDuration = computed<string>(() => {
	const ms = Math.max(0, taskEnd.value - taskStart.value);
	const seconds = Math.floor(ms / 1000);
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	const s = seconds % 60;
	if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
	if (minutes > 0) return `${minutes}:${String(s).padStart(2, "0")}`;
	return `${s}s`;
});

/** 当前活动：流式输出中的 Agent 优先，其次任务运行态 */
const activityLabel = computed<string>(() => {
	if (!taskStore.isRunning) return taskStore.chatMessages.length ? "已结束" : "等待中";
	const streaming = taskStore.streaming;
	if (streaming) return `${agentMetaOf(streaming.agentType).label} 工作中`;
	return "运行中";
});

/** 最新系统消息摘要（阶段进展线索，超出宽度截断） */
const latestSystem = computed<string>(() => {
	const list = taskStore.chatMessages;
	for (let i = list.length - 1; i >= 0; i--) {
		if (list[i].msg_type === "system" && list[i].content) {
			return list[i].content as string;
		}
	}
	return "";
});

/** 是否正在请求停止 */
const isStopping = ref(false);

/** 处理停止运行 */
async function handleStop() {
	isStopping.value = true;
	await taskStore.stopTask(props.task_id);
	isStopping.value = false;
}

// ---- Lifecycle Hooks ----

onMounted(async () => {
	await taskStore.loadTaskMessages(props.task_id);
	taskStore.connectWebSocket(props.task_id);
	const res = await getWriterSeque();
	writerSequence.value = Array.isArray(res.data) ? res.data : [];

	timer = setInterval(() => {
		if (taskStore.isRunning) now.value = Date.now();
	}, 1000);
});

onBeforeUnmount(() => {
	taskStore.closeWebSocket();
	if (timer) {
		clearInterval(timer);
		timer = null;
	}
});
</script>

<template>
  <div class="fixed inset-0">
    <ResizablePanelGroup direction="horizontal" class="h-full">
      <ResizablePanel :default-size="40" class="h-full">
        <ChatArea :messages="taskStore.chatMessages" :streaming="taskStore.streaming" />
      </ResizablePanel>
      <ResizableHandle />
      <ResizablePanel :default-size="60" class="h-full min-w-0">
        <div class="flex h-full flex-col min-w-0">
          <Tabs default-value="modeler" class="w-full h-full flex flex-col">
            <!-- 任务头条：运行状态/时长/阶段线索 + 产物区导航 -->
            <div class="border-b px-4 py-2 flex justify-between gap-4">
              <div class="flex items-center gap-3 min-w-0">
                <!-- 运行状态 -->
                <div class="flex shrink-0 items-center gap-1.5 text-sm">
                  <LoaderCircle v-if="taskStore.isRunning" class="h-3.5 w-3.5 animate-spin text-primary" />
                  <span
                    v-else
                    class="inline-block h-2 w-2 rounded-full"
                    :class="taskStore.chatMessages.length ? 'bg-emerald-500' : 'bg-muted-foreground/40'"
                  />
                  <span class="font-medium text-foreground">{{ activityLabel }}</span>
                </div>
                <!-- 真实运行时长 -->
                <span class="shrink-0 font-mono text-sm tabular-nums text-muted-foreground">
                  {{ runningDuration }}
                </span>
                <!-- 最新系统消息（阶段线索） -->
                <span class="min-w-0 truncate text-xs text-muted-foreground/70" :title="latestSystem">
                  {{ latestSystem }}
                </span>
              </div>

              <div class="flex shrink-0 items-center gap-2">
                <TabsList>
                  <TabsTrigger value="modeler" class="text-sm">建模手</TabsTrigger>
                  <TabsTrigger value="coder" class="text-sm">代码手</TabsTrigger>
                  <TabsTrigger value="writer" class="text-sm">论文手</TabsTrigger>
                </TabsList>

                <Button
                  v-if="taskStore.isRunning"
                  variant="destructive"
                  size="sm"
                  :disabled="isStopping"
                  @click="handleStop"
                >
                  {{ isStopping ? "停止中..." : "停止" }}
                </Button>
                <Button variant="outline" size="sm" @click="taskStore.downloadMessages">
                  下载
                </Button>
                <FilesSheet />
              </div>
            </div>

            <TabsContent value="modeler" class="flex-1 p-1 min-w-0 h-full overflow-hidden">
              <ModelerEditor />
            </TabsContent>
            <TabsContent value="coder" class="flex-1 p-1 min-w-0 h-full overflow-hidden">
              <CoderEditor />
            </TabsContent>
            <TabsContent value="writer" class="flex-1 p-1 min-w-0 h-full overflow-hidden">
              <WriterEditor :messages="taskStore.writerMessages" :writerSequence="writerSequence" />
            </TabsContent>
          </Tabs>
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  </div>
</template>
