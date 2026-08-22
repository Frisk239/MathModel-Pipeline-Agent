<script setup lang="ts">
import { getPendingApproval, submitApproval } from "@/apis/approvalApi";
import type { PendingApproval } from "@/apis/approvalApi";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { CheckCircle2, CircleAlert, RefreshCw } from "lucide-vue-next";
import { computed, ref, watch } from "vue";

// ---- Props & Emits ----

const props = defineProps<{ taskId: string | null }>();
const { toast } = useToast();

// ---- Reactive State ----

const open = ref(false);
const pendingData = ref<PendingApproval | null>(null);
const feedback = ref("");
const submitting = ref(false);

let pollTimer: ReturnType<typeof setInterval> | null = null;

/** 当前任务是否处于终态（终态不轮询）——由父级通过 taskId 变化驱动 */
const POLL_INTERVAL_MS = 10000;

// ---- Methods ----

async function fetchPending() {
	if (!props.taskId) return;
	try {
		const res = await getPendingApproval(props.taskId);
		if (res.data.pending && !open.value) {
			pendingData.value = res.data;
			open.value = true;
		} else if (!res.data.pending && open.value) {
			// 决策已被处理（如另一端提交），关闭弹窗
			open.value = false;
		}
	} catch {
		/* 后端不可达时静默，下轮重试 */
	}
}

async function submit(action: "approve" | "revise" | "reject") {
	if (!props.taskId) return;
	if (action === "revise" && !feedback.value.trim()) {
		toast({ title: "返工请附上修改意见", variant: "destructive" });
		return;
	}
	submitting.value = true;
	try {
		const res = await submitApproval(props.taskId, action, feedback.value.trim());
		if (res.data.success) {
			toast({ title: `已提交：${action === "approve" ? "批准" : action === "revise" ? "带意见返工" : "否决"}` });
			open.value = false;
			feedback.value = "";
		} else {
			toast({ title: res.data.message || "提交失败", variant: "destructive" });
		}
	} catch {
		toast({ title: "提交失败：无法连接后端服务", variant: "destructive" });
	} finally {
		submitting.value = false;
	}
}

function startPolling() {
	stopPolling();
	pollTimer = setInterval(fetchPending, POLL_INTERVAL_MS);
	void fetchPending();
}

function stopPolling() {
	if (pollTimer) {
		clearInterval(pollTimer);
		pollTimer = null;
	}
}

// ---- Watchers ----

watch(
	() => props.taskId,
	(newId) => {
		if (newId) startPolling();
		else stopPolling();
	},
	{ immediate: true },
);

const checkpointLabel = computed(() => {
	const cp = pendingData.value?.checkpoint ?? "";
	return (
		{
			split_review: "问题拆解",
			model_review: "建模方案",
			paper_review: "终稿",
			g2_exhausted: "代码质量门",
		}[cp] ?? cp
	);
});
</script>

<template>
  <Dialog :open="open" @update:open="(v: boolean) => { if (!v) open = false; }">
    <DialogContent class="max-w-2xl max-h-[85vh] overflow-y-auto">
      <DialogHeader>
        <DialogTitle class="flex items-center gap-2">
          <CircleAlert class="h-5 w-5 text-amber-500" />
          人工审批：{{ checkpointLabel }}
        </DialogTitle>
        <DialogDescription>
          流水线已暂停等待你的决策。无响应将一直等待，不会自动推进。
        </DialogDescription>
      </DialogHeader>

      <div v-if="pendingData?.payload" class="space-y-3 text-sm">
        <div class="font-medium">{{ pendingData.payload.title }}</div>

        <div v-if="pendingData.payload.summary" class="rounded border p-2 bg-muted/40">
          {{ pendingData.payload.summary }}
        </div>

        <div v-if="pendingData.payload.questions" class="whitespace-pre-wrap rounded border p-2">
          {{ pendingData.payload.questions }}
        </div>

        <div v-if="pendingData.payload.plan" class="whitespace-pre-wrap rounded border p-2 font-mono text-xs max-h-48 overflow-y-auto">
          {{ pendingData.payload.plan }}
        </div>

        <div v-if="pendingData.payload.ai_advisory"
          class="rounded border border-blue-200 bg-blue-50 p-2 text-xs text-blue-800">
          <span class="font-medium">AI 预审意见（仅供参考，不替你决策）：</span><br />
          {{ pendingData.payload.ai_advisory }}
        </div>

        <div v-if="pendingData.payload.g4_report" class="rounded border p-2 bg-muted/40">
          <span class="font-medium">G4 终审：</span>{{ pendingData.payload.g4_report }}
        </div>

        <ul v-if="pendingData.payload.items?.length" class="list-disc pl-5 space-y-1 text-xs">
          <li v-for="(it, i) in pendingData.payload.items" :key="i">{{ it }}</li>
        </ul>

        <div v-if="pendingData.payload.paper_preview"
          class="whitespace-pre-wrap rounded border p-2 text-xs max-h-40 overflow-y-auto">
          {{ pendingData.payload.paper_preview }}
        </div>
      </div>

      <div class="space-y-1">
        <Label for="approval-feedback" class="text-xs text-muted-foreground">
          返工意见（选择"带意见返工"时必填；意见将注入对应 Agent 的对话历史）
        </Label>
        <Textarea id="approval-feedback" v-model="feedback" rows="3"
          placeholder="例如：问题二改用 XGBoost 并补充交叉验证……" />
      </div>

      <div class="flex justify-end gap-2 pt-2 border-t">
        <Button variant="destructive" size="sm" :disabled="submitting" @click="submit('reject')">
          否决任务
        </Button>
        <Button variant="secondary" size="sm" :disabled="submitting" @click="submit('revise')">
          <RefreshCw class="h-4 w-4 mr-1" /> 带意见返工
        </Button>
        <Button size="sm" :disabled="submitting" @click="submit('approve')">
          <CheckCircle2 class="h-4 w-4 mr-1" /> 批准继续
        </Button>
      </div>
    </DialogContent>
  </Dialog>
</template>
