<script setup lang="ts">
import {
	getBackendConfig,
	getModelCapability,
	listModels,
	probeReasoning,
	saveApiConfig,
	validateApiKey,
	validateOpenalexEmail,
} from "@/apis/apiKeyApi";
import type { HilConfig } from "@/apis/apiKeyApi";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
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
import { useApiKeyStore } from "@/stores/apiKeys";
import type { ModelConfig } from "@/utils/interface";
import { CheckCircle, RefreshCw, Search, XCircle } from "lucide-vue-next";
import { computed, onMounted, ref } from "vue";

// ---- Props & Emits ----

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<(e: "update:open", value: boolean) => void>();

const { toast } = useToast();

// ---- Reactive State ----

const apiKeyStore = useApiKeyStore();

/** API 类型选项 */
const apiTypeOptions = [
	{ value: "openai-chat", label: "OpenAI Chat" },
	{ value: "openai-responses", label: "OpenAI Responses" },
	{ value: "anthropic", label: "Anthropic" },
];

/** Agent 表单配置 */
interface AgentFormConfig {
	apiKey: string;
	baseUrl: string;
	modelId: string;
	apiType: string;
	contextWindow: number;
	reasoningEffort: string;
	thinkingBudget: number | null;
	fallbackModels: string;
}

/** Agent 键名 */
type AgentKey = "coordinator" | "modeler" | "coder" | "writer";

/** 本地表单数据 */
const form = ref<{
	coordinator: AgentFormConfig;
	modeler: AgentFormConfig;
	coder: AgentFormConfig;
	writer: AgentFormConfig;
	openalex_email: string;
}>({
	coordinator: {
		apiKey: "",
		baseUrl: "",
		modelId: "",
		apiType: "",
		contextWindow: 128000,
		reasoningEffort: "",
		thinkingBudget: null,
		fallbackModels: "",
	},
	modeler: {
		apiKey: "",
		baseUrl: "",
		modelId: "",
		apiType: "",
		contextWindow: 128000,
		reasoningEffort: "",
		thinkingBudget: null,
		fallbackModels: "",
	},
	coder: {
		apiKey: "",
		baseUrl: "",
		modelId: "",
		apiType: "",
		contextWindow: 128000,
		reasoningEffort: "",
		thinkingBudget: null,
		fallbackModels: "",
	},
	writer: {
		apiKey: "",
		baseUrl: "",
		modelId: "",
		apiType: "",
		contextWindow: 128000,
		reasoningEffort: "",
		thinkingBudget: null,
		fallbackModels: "",
	},
	openalex_email: "",
});

/** 验证加载状态 */
const validating = ref(false);

/** 人工检查点配置（全局，保存时随配置提交后端） */
const hilConfig = ref<Required<HilConfig>>({
	autoMode: false,
	problem_split: true,
	model_selection: true,
	code_review: false,
	paper_review: true,
});

/** 思考档位全集（未探测时下拉展示全部） */
const ALL_EFFORTS = [
	"off",
	"minimal",
	"low",
	"medium",
	"high",
	"max",
	"xhigh",
];

/** 各 Agent 探测到的模型列表 */
const detectedModels = ref<Record<string, string[]>>({});
/** 各 Agent 模型列表探测中 */
const probingModels = ref<Record<string, boolean>>({});
/** 各 Agent 探测到的思考档位 */
const effortOptions = ref<Record<string, string[]>>({});
/** 各 Agent 思考档位探测中 */
const probingEfforts = ref<Record<string, boolean>>({});
/** 各 Agent 思考档位探测结果提示 */
const effortProbeMsg = ref<Record<string, string>>({});

/** 探测指定 Agent 的 Base URL 可用模型列表 */
const detectModels = async (key: AgentKey) => {
	const cfg = form.value[key];
	if (!cfg.apiKey) {
		toast({ title: "请先填写 API Key", variant: "destructive" });
		return;
	}
	probingModels.value[key] = true;
	try {
		const res = await listModels({
			api_key: cfg.apiKey,
			base_url: cfg.baseUrl || "https://api.openai.com/v1",
			api_type: cfg.apiType || "openai-chat",
		});
		if (res.data.success) {
			detectedModels.value[key] = res.data.models;
			toast({ title: res.data.message });
			// 已选模型时优先查能力缓存，未命中再探测
			if (cfg.modelId) {
				await loadEffortOptions(key);
			}
		} else {
			toast({ title: res.data.message, variant: "destructive" });
		}
	} catch {
		toast({ title: "模型探测失败: 无法连接后端服务", variant: "destructive" });
	} finally {
		probingModels.value[key] = false;
	}
};

/** 应用档位列表：当前所选档位不在支持列表时回退为默认 */
const applyEffortOptions = (
	key: AgentKey,
	supported: string[],
	message: string,
) => {
	effortOptions.value[key] = supported;
	const cfg = form.value[key];
	if (cfg.reasoningEffort && !supported.includes(cfg.reasoningEffort)) {
		cfg.reasoningEffort = "";
	}
	effortProbeMsg.value[key] = message;
};

/** 优先查能力缓存，未命中时执行真实探测 */
const loadEffortOptions = async (key: AgentKey) => {
	const cfg = form.value[key];
	try {
		const cached = await getModelCapability({
			base_url: cfg.baseUrl || "https://api.openai.com/v1",
			model_id: cfg.modelId,
			api_type: cfg.apiType || "openai-chat",
		});
		if (cached.data.found) {
			applyEffortOptions(key, cached.data.supported, "✓ 已应用缓存的能力数据");
			return;
		}
	} catch {
		// 缓存查询失败不阻塞，继续走探测
	}
	await probeEfforts(key, false);
};

/** 探测指定 Agent 当前模型的思考深度档位（force 时忽略缓存） */
const probeEfforts = async (key: AgentKey, force = false) => {
	const cfg = form.value[key];
	if (!cfg.apiKey || !cfg.modelId) {
		effortProbeMsg.value[key] = "需先填写 API Key 和 Model ID";
		return;
	}
	probingEfforts.value[key] = true;
	try {
		const res = await probeReasoning({
			api_key: cfg.apiKey,
			base_url: cfg.baseUrl || "https://api.openai.com/v1",
			model_id: cfg.modelId,
			api_type: cfg.apiType || "openai-chat",
			force,
		});
		if (res.data.success) {
			applyEffortOptions(key, res.data.supported, res.data.message);
		} else {
			effortProbeMsg.value[key] = res.data.message;
		}
	} catch {
		effortProbeMsg.value[key] = "探测失败: 无法连接后端服务";
	} finally {
		probingEfforts.value[key] = false;
	}
};

/** 思考深度下拉选项（探测后只展示支持的档位） */
const effortOptionsFor = (key: string) => {
	const list = effortOptions.value[key] ?? ALL_EFFORTS;
	return [
		{ value: "default", label: "默认" },
		...list.map((v) => ({ value: v, label: v })),
	];
};

/** reka-ui 不允许空 value，用 default 哨兵表示"默认" */
const effortValue = (key: AgentKey) =>
	form.value[key].reasoningEffort || "default";

const handleEffortChange = (key: AgentKey, value: string) => {
	form.value[key].reasoningEffort = value === "default" ? "" : value;
};

/** 各配置项的验证结果 */
const validationResults = ref({
	coordinator: { valid: false, message: "" },
	modeler: { valid: false, message: "" },
	coder: { valid: false, message: "" },
	writer: { valid: false, message: "" },
	openalex_email: { valid: false, message: "" },
});

// ---- Computed ----

/** 模型配置列表 */
const modelConfigs = computed<{ key: AgentKey; label: string }[]>(() => [
	{ key: "coordinator", label: "协调者模型配置" },
	{ key: "modeler", label: "建模手模型配置" },
	{ key: "coder", label: "代码手模型配置" },
	{ key: "writer", label: "论文手模型配置" },
]);

// ---- Methods ----

/** 从 store 加载数据到表单（旧持久化数据缺字段时回填默认值） */
const fromStoreConfig = (config: ModelConfig): AgentFormConfig => ({
	...config,
	contextWindow: config.contextWindow ?? 128000,
	reasoningEffort: config.reasoningEffort ?? "",
	thinkingBudget: config.thinkingBudget ?? null,
	fallbackModels: config.fallbackModels ?? "",
});

const loadFromStore = () => {
	form.value.coordinator = fromStoreConfig(apiKeyStore.coordinatorConfig);
	form.value.modeler = fromStoreConfig(apiKeyStore.modelerConfig);
	form.value.coder = fromStoreConfig(apiKeyStore.coderConfig);
	form.value.writer = fromStoreConfig(apiKeyStore.writerConfig);
	form.value.openalex_email = apiKeyStore.openalexEmail;
};

/** 保存表单数据到 store 和后端 */
const saveToStore = async () => {
	apiKeyStore.setCoordinatorConfig(form.value.coordinator);
	apiKeyStore.setModelerConfig(form.value.modeler);
	apiKeyStore.setCoderConfig(form.value.coder);
	apiKeyStore.setWriterConfig(form.value.writer);
	apiKeyStore.setOpenalexEmail(form.value.openalex_email);
	try {
		await saveApiConfig({
			coordinator: form.value.coordinator,
			modeler: form.value.modeler,
			coder: form.value.coder,
			writer: form.value.writer,
			openalex_email: form.value.openalex_email,
			hil_config: { ...hilConfig.value },
		});
	} catch (error) {
		console.error("保存配置到后端失败:", error);
	}
};

// ---- Lifecycle Hooks ----

onMounted(async () => {
	loadFromStore();
	try {
		const res = await getBackendConfig();
		if (res.data.hil) {
			hilConfig.value = {
				autoMode: !!res.data.hil.autoMode,
				problem_split: !!res.data.hil.problem_split,
				model_selection: !!res.data.hil.model_selection,
				code_review: !!res.data.hil.code_review,
				paper_review: !!res.data.hil.paper_review,
			};
		}
	} catch {
		/* 配置回显失败用默认值 */
	}
});

// ---- Methods (continued) ----

/** 更新弹窗开关状态 */
const updateOpen = (value: boolean) => {
	emit("update:open", value);
};

/** 保存并关闭弹窗 */
const saveAndClose = async () => {
	await saveToStore();
	updateOpen(false);
};

/** 验证大模型 API Key */
const validateModelApiKey = async (config: {
	apiKey: string;
	baseUrl: string;
	modelId: string;
	apiType: string;
}) => {
	if (!config.apiKey) {
		return { valid: false, message: "API Key 为空" };
	}

	if (!config.modelId) {
		return { valid: false, message: "Model ID 为空" };
	}

	try {
		const result = await validateApiKey({
			api_key: config.apiKey,
			base_url: config.baseUrl || "https://api.openai.com/v1",
			model_id: config.modelId,
			api_type: config.apiType || "openai-chat",
		});

		return {
			valid: result.data.valid,
			message: result.data.message,
		};
	} catch (error) {
		return {
			valid: false,
			message: "✗ 验证失败: 无法连接到验证服务",
		};
	}
};

/** 一键验证所有 API Keys */
const validateAllApiKeys = async () => {
	validating.value = true;

	validationResults.value = {
		coordinator: { valid: false, message: "" },
		modeler: { valid: false, message: "" },
		coder: { valid: false, message: "" },
		writer: { valid: false, message: "" },
		openalex_email: { valid: false, message: "" },
	};

	try {
		for (const config of modelConfigs.value) {
			const key = config.key as keyof typeof validationResults.value;
			const formKey = config.key as keyof typeof form.value;

			validationResults.value[key] = { valid: false, message: "验证中..." };
			validationResults.value[key] = await validateModelApiKey(
				form.value[formKey] as {
					apiKey: string;
					baseUrl: string;
					modelId: string;
					apiType: string;
				},
			);

			await new Promise((resolve) => setTimeout(resolve, 1000));
		}

		validationResults.value.openalex_email = await validateOpenalexEmail({
			email: form.value.openalex_email,
		}).then((res) => res.data);
	} catch (error) {
		console.error("验证过程中发生错误:", error);
		for (const key of Object.keys(validationResults.value)) {
			if (
				!validationResults.value[key as keyof typeof validationResults.value]
					.message
			) {
				validationResults.value[key as keyof typeof validationResults.value] = {
					valid: false,
					message: "验证过程中发生未知错误",
				};
			}
		}
	} finally {
		validating.value = false;
	}
};

/** 重置所有表单数据 */
const resetAll = () => {
	form.value = {
		coordinator: {
			apiKey: "",
			baseUrl: "",
			modelId: "",
			apiType: "",
			contextWindow: 128000,
			reasoningEffort: "",
			thinkingBudget: null,
			fallbackModels: "",
		},
		modeler: {
			apiKey: "",
			baseUrl: "",
			modelId: "",
			apiType: "",
			contextWindow: 128000,
			reasoningEffort: "",
			thinkingBudget: null,
			fallbackModels: "",
		},
		coder: {
			apiKey: "",
			baseUrl: "",
			modelId: "",
			apiType: "",
			contextWindow: 128000,
			reasoningEffort: "",
			thinkingBudget: null,
			fallbackModels: "",
		},
		writer: {
			apiKey: "",
			baseUrl: "",
			modelId: "",
			apiType: "",
			contextWindow: 128000,
			reasoningEffort: "",
			thinkingBudget: null,
			fallbackModels: "",
		},
		openalex_email: "",
	};
};
</script>

<template>
  <Dialog :open="props.open" @update:open="updateOpen">
    <DialogContent class="max-w-xl max-h-[85vh] overflow-y-auto">
      <DialogHeader>
        <DialogTitle>设置</DialogTitle>
        <DialogDescription>
          为每个 Agent 配置 API 类型和模型
        </DialogDescription>
      </DialogHeader>

      <div class="space-y-4 py-2">

        <!-- Models Configurations -->
        <div v-for="config in modelConfigs" :key="config.key" class="space-y-2">
          <h3 class="text-sm font-medium">{{ config.label }}</h3>
          <div class="grid grid-cols-2 gap-2">
            <div class="space-y-1">
              <Label :for="`${config.key}-api-type`" class="text-xs text-muted-foreground">API 类型</Label>
              <Select :model-value="(form as any)[config.key].apiType"
                @update:model-value="(value: any) => { (form as any)[config.key].apiType = value }">
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
              <Label :for="`${config.key}-api-key`" class="text-xs text-muted-foreground">API Key</Label>
              <Input :id="`${config.key}-api-key`" v-model.trim="(form as any)[config.key].apiKey" type="password"
                placeholder="请输入 API Key" class="h-7 text-xs flex-1" />
              <div v-if="validationResults[config.key as keyof typeof validationResults].message"
                class="flex items-center">
                <CheckCircle v-if="validationResults[config.key as keyof typeof validationResults].valid"
                  class="h-4 w-4 text-green-500" />
                <XCircle v-else class="h-4 w-4 text-red-500" />
              </div>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2">
            <div class="space-y-1">
              <Label :for="`${config.key}-base-url`" class="text-xs text-muted-foreground">Base URL</Label>
              <Input :id="`${config.key}-base-url`" v-model.trim="(form as any)[config.key].baseUrl"
                placeholder="https://api.openai.com/v1" class="h-7 text-xs" />
            </div>
            <div class="space-y-1">
              <Label :for="`${config.key}-model-id`" class="text-xs text-muted-foreground">Model ID</Label>
              <div class="flex gap-1">
                <Input :id="`${config.key}-model-id`" v-model.trim="(form as any)[config.key].modelId"
                  :list="`${config.key}-models`"
                  placeholder="gpt-4o / claude-sonnet-4-20250514" class="h-7 text-xs flex-1" />
                <Button variant="outline" class="h-7 w-9 p-0 shrink-0" :disabled="probingModels[config.key]"
                  title="探测该 Base URL 的可用模型" @click="detectModels(config.key)">
                  <RefreshCw :class="['h-3.5 w-3.5', probingModels[config.key] && 'animate-spin']" />
                </Button>
              </div>
              <datalist :id="`${config.key}-models`">
                <option v-for="m in detectedModels[config.key] ?? []" :key="m" :value="m" />
              </datalist>
            </div>
          </div>

          <div class="space-y-1">
            <Label :for="`${config.key}-fallback-models`" class="text-xs text-muted-foreground">备用模型</Label>
            <Input :id="`${config.key}-fallback-models`" v-model.trim="(form as any)[config.key].fallbackModels"
              placeholder="model-b, model-c" class="h-7 text-xs" />
          </div>

          <div class="grid grid-cols-2 gap-2">
            <div class="space-y-1">
              <Label :for="`${config.key}-reasoning-effort`" class="text-xs text-muted-foreground">思考深度</Label>
              <div class="flex gap-1">
                <Select :model-value="effortValue(config.key)"
                  @update:model-value="(value: any) => handleEffortChange(config.key, value)">
                  <SelectTrigger class="w-full h-7 text-xs">
                    <SelectValue placeholder="默认" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectLabel>思考深度</SelectLabel>
                      <SelectItem v-for="opt in effortOptionsFor(config.key)" :key="opt.value" :value="opt.value">
                        {{ opt.label }}
                      </SelectItem>
                    </SelectGroup>
                  </SelectContent>
                </Select>
                <Button variant="outline" class="h-7 w-9 p-0 shrink-0" :disabled="probingEfforts[config.key]"
                  title="强制重新探测该模型的思考深度" @click="probeEfforts(config.key, true)">
                  <Search :class="['h-3.5 w-3.5', probingEfforts[config.key] && 'animate-pulse']" />
                </Button>
              </div>
              <p v-if="effortProbeMsg[config.key]" class="text-xs text-muted-foreground break-all">
                {{ effortProbeMsg[config.key] }}
              </p>
            </div>
            <div class="space-y-1">
              <Label :for="`${config.key}-thinking-budget`" class="text-xs text-muted-foreground">思考预算（token）</Label>
              <Input :id="`${config.key}-thinking-budget`"
                v-model.number="(form as any)[config.key].thinkingBudget" type="number"
                placeholder="仅 Anthropic 协议生效，如 16384" class="h-7 text-xs" min="1024" step="1024" />
            </div>
          </div>
          <div class="space-y-1">
            <Label :for="`${config.key}-context-window`" class="text-xs text-muted-foreground">
              上下文窗口（token）
            </Label>
            <Input :id="`${config.key}-context-window`"
              v-model.number="(form as any)[config.key].contextWindow" type="number"
              placeholder="128000" class="h-7 text-xs" min="4096" step="1024" />
          </div>
          <div v-if="validationResults[config.key as keyof typeof validationResults].message" :class="[
            'text-xs px-2 py-1 rounded text-left border',
            validationResults[config.key as keyof typeof validationResults].valid ? 'bg-green-50 text-green-700 border-green-200' : 'bg-red-50 text-red-700 border-red-200'
          ]">
            {{ validationResults[config.key as keyof typeof validationResults].message }}
          </div>
        </div>
      </div>

      <div class="space-y-2">
        <h3 class="text-sm font-medium">流程审批（人工检查点）</h3>
        <div class="text-xs text-muted-foreground">
          开启后流水线在对应阶段暂停等待人工审批；审批无响应将一直等待。全自动模式将跳过所有人工检查点。
        </div>
        <div class="grid grid-cols-2 gap-2">
          <label class="flex items-center gap-2 text-xs">
            <input type="checkbox" v-model="hilConfig.autoMode" class="accent-primary" />
            全自动模式（跳过全部检查点）
          </label>
          <label class="flex items-center gap-2 text-xs">
            <input type="checkbox" v-model="hilConfig.problem_split" :disabled="hilConfig.autoMode" />
            ① 拆题后审批
          </label>
          <label class="flex items-center gap-2 text-xs">
            <input type="checkbox" v-model="hilConfig.model_selection" :disabled="hilConfig.autoMode" />
            ② 建模方案后审批
          </label>
          <label class="flex items-center gap-2 text-xs">
            <input type="checkbox" v-model="hilConfig.paper_review" :disabled="hilConfig.autoMode" />
            ④ 终稿前审批
          </label>
        </div>
      </div>

      <div class="space-y-2">
        <h3 class="text-sm font-medium">其他</h3>
        <Label :for="`openalex-email`" class="text-xs text-muted-foreground">OpenAlex Email</Label>
        <div class="text-xs text-muted-foreground">
          使用 email 注册账号从 <a href="https://openalex.org/" target="_blank"
            class="text-blue-600 hover:text-blue-800 underline text-xs">OpenAlex</a> 获取访问文献权利
        </div>
        <Input :id="`openalex-email`" v-model.trim="form.openalex_email" placeholder="请输入 OpenAlex Email"
          class="h-7 text-xs flex-1" />
        <div v-if="validationResults.openalex_email.message" :class="[
          'text-xs px-2 py-1 rounded text-left border',
          validationResults.openalex_email.valid ? 'bg-green-50 text-green-700 border-green-200' : 'bg-red-50 text-red-700 border-red-200'
        ]">
          {{ validationResults.openalex_email.message }}
        </div>
      </div>

      <div class="flex justify-between items-center pt-3 border-t">
        <div class="flex justify-between items-center gap-2">
          <Button @click="validateAllApiKeys" :disabled="validating" class="h-7 text-xs px-3" variant="secondary">
            {{ validating ? '验证中...' : '一键验证' }}
          </Button>
          <Button @click="resetAll" class="h-7 text-xs px-3" variant="secondary">
            重置
          </Button>
        </div>
        <div class="flex space-x-2">
          <Button variant="outline" @click="updateOpen(false)" class="h-7 text-xs px-3">
            取消
          </Button>
          <Button @click="saveAndClose" class="h-7 text-xs px-3">
            保存
          </Button>
        </div>
      </div>
    </DialogContent>
  </Dialog>
</template>
