<script setup lang="ts">
import {
	getModelCapability,
	listModels,
	probeReasoning,
} from "@/apis/apiKeyApi";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectGroup,
	SelectItem,
	SelectLabel,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";
import { RefreshCw, Search } from "lucide-vue-next";
import { computed, ref, watch } from "vue";

// 单个 Agent 的模型配置表单卡：API 类型/Key/BaseURL/模型/备用/思考深度/预算/窗口，
// 含模型列表与思考档位探测（探测状态卡片内自包含）。

export interface AgentFormConfig {
	apiKey: string;
	baseUrl: string;
	modelId: string;
	apiType: string;
	contextWindow: number;
	reasoningEffort: string;
	thinkingBudget: number | null;
	fallbackModels: string;
}

export function emptyAgentForm(): AgentFormConfig {
	return {
		apiKey: "",
		baseUrl: "",
		modelId: "",
		apiType: "",
		contextWindow: 128000,
		reasoningEffort: "",
		thinkingBudget: null,
		fallbackModels: "",
	};
}

/** 思考档位全集（未探测时下拉展示全部） */
const ALL_EFFORTS = ["off", "minimal", "low", "medium", "high", "max", "xhigh"];

const apiTypeOptions = [
	{ value: "openai-chat", label: "OpenAI Chat" },
	{ value: "openai-responses", label: "OpenAI Responses" },
	{ value: "anthropic", label: "Anthropic" },
];

const props = defineProps<{
	label: string;
	/** 一键验证的结果展示（由父级持有） */
	validationResult?: { valid: boolean; message: string };
}>();

const config = defineModel<AgentFormConfig>({ required: true });

const { toast } = useToast();

// ---- 探测状态（卡片内自包含） ----

const detectedModels = ref<string[]>([]);
const probingModels = ref(false);
const effortOptions = ref<string[] | null>(null);
const probingEfforts = ref(false);
const effortProbeMsg = ref("");

const effortSelectOptions = () => [
	{ value: "default", label: "默认" },
	...(effortOptions.value ?? ALL_EFFORTS).map((v) => ({ value: v, label: v })),
];

// reka-ui 不允许空 value，用 default 哨兵表示"默认"
const effortValue = () => config.value.reasoningEffort || "default";

const handleEffortChange = (value: unknown) => {
	const v = String(value);
	config.value.reasoningEffort = v === "default" ? "" : v;
};

/** 思考预算输入桥接：Input 不接受 null，空值转 undefined↔null */
const budgetModel = computed({
	get: () => config.value.thinkingBudget ?? undefined,
	set: (v: string | number | undefined) => {
		config.value.thinkingBudget =
			v === undefined || v === "" ? null : Number(v);
	},
});

/** 应用档位列表：当前所选档位不在支持列表时回退为默认 */
const applyEffortOptions = (supported: string[], message: string) => {
	effortOptions.value = supported;
	if (
		config.value.reasoningEffort &&
		!supported.includes(config.value.reasoningEffort)
	) {
		config.value.reasoningEffort = "";
	}
	effortProbeMsg.value = message;
};

const detectModels = async () => {
	if (!config.value.apiKey) {
		toast({ title: "请先填写 API Key", variant: "destructive" });
		return;
	}
	probingModels.value = true;
	try {
		const res = await listModels({
			api_key: config.value.apiKey,
			base_url: config.value.baseUrl || "https://api.openai.com/v1",
			api_type: config.value.apiType || "openai-chat",
		});
		if (res.data.success) {
			detectedModels.value = res.data.models;
			toast({ title: res.data.message });
			// 已选模型时优先查能力缓存，未命中再探测
			if (config.value.modelId) {
				await loadEffortOptions();
			}
		} else {
			toast({ title: res.data.message, variant: "destructive" });
		}
	} catch {
		toast({ title: "模型探测失败: 无法连接后端服务", variant: "destructive" });
	} finally {
		probingModels.value = false;
	}
};

/** 优先查能力缓存，未命中时执行真实探测 */
const loadEffortOptions = async () => {
	try {
		const cached = await getModelCapability({
			base_url: config.value.baseUrl || "https://api.openai.com/v1",
			model_id: config.value.modelId,
			api_type: config.value.apiType || "openai-chat",
		});
		if (cached.data.found) {
			applyEffortOptions(cached.data.supported, "已应用缓存的能力数据");
			return;
		}
	} catch {
		// 缓存查询失败不阻塞，继续走探测
	}
	await probeEfforts(false);
};

/** 探测当前模型的思考深度档位（force 时忽略缓存） */
const probeEfforts = async (force = false) => {
	if (!config.value.apiKey || !config.value.modelId) {
		effortProbeMsg.value = "需先填写 API Key 和 Model ID";
		return;
	}
	probingEfforts.value = true;
	try {
		const res = await probeReasoning({
			api_key: config.value.apiKey,
			base_url: config.value.baseUrl || "https://api.openai.com/v1",
			model_id: config.value.modelId,
			api_type: config.value.apiType || "openai-chat",
			force,
		});
		if (res.data.success) {
			applyEffortOptions(res.data.supported, res.data.message);
		} else {
			effortProbeMsg.value = res.data.message;
		}
	} catch {
		effortProbeMsg.value = "探测失败: 无法连接后端服务";
	} finally {
		probingEfforts.value = false;
	}
};

// 切换模型时优先拉缓存档位（静默，不弹探测请求）
watch(
	() => config.value.modelId,
	(mid) => {
		if (mid) loadEffortOptions();
	},
);
</script>

<template>
  <div class="space-y-2">
    <h3 class="text-sm font-medium">{{ props.label }}</h3>
    <div class="grid grid-cols-2 gap-2">
      <div class="space-y-1">
        <Label class="text-xs text-muted-foreground">API 类型</Label>
        <Select v-model="config.apiType">
          <SelectTrigger class="w-full h-7 text-xs">
            <SelectValue placeholder="选择 API 类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectLabel>API 类型</SelectLabel>
              <SelectItem v-for="opt in apiTypeOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
      </div>
      <div class="space-y-1">
        <Label class="text-xs text-muted-foreground">API Key</Label>
        <Input v-model.trim="config.apiKey" type="password" placeholder="请输入 API Key"
          class="h-7 text-xs flex-1" />
      </div>
    </div>

    <div class="grid grid-cols-2 gap-2">
      <div class="space-y-1">
        <Label class="text-xs text-muted-foreground">Base URL</Label>
        <Input v-model.trim="config.baseUrl" placeholder="https://api.openai.com/v1" class="h-7 text-xs" />
      </div>
      <div class="space-y-1">
        <Label class="text-xs text-muted-foreground">Model ID</Label>
        <div class="flex gap-1">
          <Input v-model.trim="config.modelId" list="model-options"
            placeholder="gpt-4o / claude-sonnet-4-20250514" class="h-7 text-xs flex-1" />
          <Button variant="outline" class="h-7 w-9 p-0 shrink-0" :disabled="probingModels"
            title="探测该 Base URL 的可用模型" @click="detectModels">
            <RefreshCw :class="['h-3.5 w-3.5', probingModels && 'animate-spin']" />
          </Button>
        </div>
        <datalist id="model-options">
          <option v-for="m in detectedModels" :key="m" :value="m" />
        </datalist>
      </div>
    </div>

    <div class="space-y-1">
      <Label class="text-xs text-muted-foreground">备用模型</Label>
      <Input v-model.trim="config.fallbackModels" placeholder="model-b, model-c" class="h-7 text-xs" />
    </div>

    <div class="grid grid-cols-2 gap-2">
      <div class="space-y-1">
        <Label class="text-xs text-muted-foreground">思考深度</Label>
        <div class="flex gap-1">
          <Select :model-value="effortValue()" @update:model-value="handleEffortChange">
            <SelectTrigger class="w-full h-7 text-xs">
              <SelectValue placeholder="默认" />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectLabel>思考深度</SelectLabel>
                <SelectItem v-for="opt in effortSelectOptions()" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </SelectItem>
              </SelectGroup>
            </SelectContent>
          </Select>
          <Button variant="outline" class="h-7 w-9 p-0 shrink-0" :disabled="probingEfforts"
            title="强制重新探测该模型的思考深度" @click="probeEfforts(true)">
            <Search :class="['h-3.5 w-3.5', probingEfforts && 'animate-pulse']" />
          </Button>
        </div>
        <p v-if="effortProbeMsg" class="text-xs text-muted-foreground break-all">
          {{ effortProbeMsg }}
        </p>
      </div>
      <div class="space-y-1">
        <Label class="text-xs text-muted-foreground">思考预算（token）</Label>
        <Input v-model.number="budgetModel" type="number"
          placeholder="仅 Anthropic 协议生效，如 16384" class="h-7 text-xs" min="1024" step="1024" />
      </div>
    </div>
    <div class="space-y-1">
      <Label class="text-xs text-muted-foreground">上下文窗口（token）</Label>
      <Input v-model.number="config.contextWindow" type="number"
        placeholder="128000" class="h-7 text-xs" min="4096" step="1024" />
    </div>
    <div v-if="props.validationResult?.message" :class="[
      'text-xs px-2 py-1 rounded text-left border',
      props.validationResult.valid ? 'bg-green-50 text-green-700 border-green-200' : 'bg-red-50 text-red-700 border-red-200'
    ]">
      {{ props.validationResult.message }}
    </div>
  </div>
</template>
