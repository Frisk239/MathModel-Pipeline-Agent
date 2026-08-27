<script setup lang="ts">
import {
	getBackendConfig,
	saveApiConfig,
	validateApiKey,
	validateOpenalexEmail,
} from "@/apis/apiKeyApi";
import type { HilConfig } from "@/apis/apiKeyApi";
import AgentFormCard from "@/components/AgentFormCard.vue";
import type { AgentFormConfig } from "@/components/AgentFormCard.vue";
import { emptyAgentForm } from "@/components/AgentFormCard.vue";
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
import { useApiKeyStore } from "@/stores/apiKeys";
import type { ModelConfig } from "@/utils/interface";
import { onMounted, ref } from "vue";

// ---- Props & Emits ----

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<(e: "update:open", value: boolean) => void>();

// ---- Reactive State ----

const apiKeyStore = useApiKeyStore();

/** Agent 键名 */
type AgentKey = "coordinator" | "modeler" | "coder" | "writer";

const agentCards: { key: AgentKey; label: string }[] = [
	{ key: "coordinator", label: "协调者模型配置" },
	{ key: "modeler", label: "建模手模型配置" },
	{ key: "coder", label: "代码手模型配置" },
	{ key: "writer", label: "论文手模型配置" },
];

/** 本地表单数据 */
const form = ref<Record<AgentKey, AgentFormConfig> & { openalex_email: string }>({
	coordinator: emptyAgentForm(),
	modeler: emptyAgentForm(),
	coder: emptyAgentForm(),
	writer: emptyAgentForm(),
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

/** 各配置项的验证结果 */
const validationResults = ref<Record<AgentKey | "openalex_email", { valid: boolean; message: string }>>({
	coordinator: { valid: false, message: "" },
	modeler: { valid: false, message: "" },
	coder: { valid: false, message: "" },
	writer: { valid: false, message: "" },
	openalex_email: { valid: false, message: "" },
});

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
const validateModelApiKey = async (config: AgentFormConfig) => {
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
	} catch {
		return {
			valid: false,
			message: "验证失败: 无法连接到验证服务",
		};
	}
};

/** 一键验证所有 API Keys */
const validateAllApiKeys = async () => {
	validating.value = true;

	for (const key of Object.keys(validationResults.value) as (keyof typeof validationResults.value)[]) {
		validationResults.value[key] = { valid: false, message: "" };
	}

	try {
		for (const { key } of agentCards) {
			validationResults.value[key] = { valid: false, message: "验证中..." };
			validationResults.value[key] = await validateModelApiKey(form.value[key]);
			await new Promise((resolve) => setTimeout(resolve, 1000));
		}

		validationResults.value.openalex_email = await validateOpenalexEmail({
			email: form.value.openalex_email,
		}).then((res) => res.data);
	} catch (error) {
		console.error("验证过程中发生错误:", error);
		for (const key of Object.keys(validationResults.value) as (keyof typeof validationResults.value)[]) {
			if (!validationResults.value[key].message) {
				validationResults.value[key] = {
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
		coordinator: emptyAgentForm(),
		modeler: emptyAgentForm(),
		coder: emptyAgentForm(),
		writer: emptyAgentForm(),
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
        <AgentFormCard v-for="card in agentCards" :key="card.key" v-model="form[card.key]" :label="card.label"
          :validation-result="validationResults[card.key]" />
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
        <Label for="openalex-email" class="text-xs text-muted-foreground">OpenAlex Email</Label>
        <div class="text-xs text-muted-foreground">
          使用 email 注册账号从 <a href="https://openalex.org/" target="_blank"
            class="text-blue-600 hover:text-blue-800 underline text-xs">OpenAlex</a> 获取访问文献权利
        </div>
        <Input id="openalex-email" v-model.trim="form.openalex_email" placeholder="请输入 OpenAlex Email"
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
