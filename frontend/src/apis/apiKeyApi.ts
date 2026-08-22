import request from "@/utils/request";

/** 验证 API Key 请求参数 */
export interface ValidateApiKeyRequest {
	api_key: string;
	base_url?: string;
	model_id: string;
	api_type?: string;
}

/** 验证 API Key 响应 */
export interface ValidateApiKeyResponse {
	valid: boolean;
	message: string;
}

/** 单个 Agent 的配置载荷（camelCase，与后端 save-api-config 读取的字段对齐） */
export interface AgentConfigPayload {
	apiKey: string;
	baseUrl: string;
	modelId: string;
	apiType: string;
	contextWindow?: number;
	reasoningEffort?: string;
	thinkingBudget?: number | null;
}

/** 人工检查点配置 */
export interface HilConfig {
	autoMode?: boolean;
	problem_split?: boolean;
	model_selection?: boolean;
	code_review?: boolean;
	paper_review?: boolean;
}

/** 保存 API 配置请求参数 */
export interface SaveApiConfigRequest {
	coordinator: AgentConfigPayload;
	modeler: AgentConfigPayload;
	coder: AgentConfigPayload;
	writer: AgentConfigPayload;
	openalex_email: string;
	hil_config?: HilConfig;
}

/** 验证 OpenAlex Email 请求参数 */
export interface ValidateOpenalexEmailRequest {
	email: string;
}

/** 验证 OpenAlex Email 响应 */
export interface ValidateOpenalexEmailResponse {
	valid: boolean;
	message: string;
}

/** 模型列表探测请求参数 */
export interface ListModelsRequest {
	api_key: string;
	base_url?: string;
	api_type?: string;
}

/** 模型列表探测响应 */
export interface ListModelsResponse {
	success: boolean;
	models: string[];
	message: string;
}

/** 思考档位探测请求参数 */
export interface ProbeReasoningRequest {
	api_key: string;
	base_url?: string;
	model_id: string;
	api_type?: string;
	/** 强制重新探测（忽略缓存） */
	force?: boolean;
}

/** 思考档位探测响应 */
export interface ProbeReasoningResponse {
	success: boolean;
	supported: string[];
	message: string;
}

/** 能力缓存查询请求参数 */
export interface GetCapabilityRequest {
	base_url: string;
	model_id: string;
	api_type?: string;
}

/** 能力缓存查询响应 */
export interface GetCapabilityResponse {
	found: boolean;
	supported: string[];
	probed_at: number | null;
}

/**
 * 验证 API Key 是否有效
 * @param params 验证请求参数
 */
export function validateApiKey(params: ValidateApiKeyRequest) {
	return request.post<ValidateApiKeyResponse>("/validate-api-key", params);
}

/**
 * 验证 OpenAlex Email 是否有效
 * @param params 验证请求参数
 */
export function validateOpenalexEmail(params: ValidateOpenalexEmailRequest) {
	return request.post<ValidateOpenalexEmailResponse>(
		"/validate-openalex-email",
		params,
	);
}

/**
 * 保存 API 配置到后端
 * @param params API 配置参数
 */
export function saveApiConfig(params: SaveApiConfigRequest) {
	return request.post<{ success: boolean; message: string }>(
		"/save-api-config",
		params,
	);
}

/**
 * 获取后端全局配置（含人工检查点开关）
 */
export function getBackendConfig() {
	return request.get<{
		environment: string;
		hil: HilConfig & { enabled: boolean };
	}>("/config");
}

/**
 * 探测 Base URL 在指定协议下可用的模型列表
 * @param params 探测请求参数
 */
export function listModels(params: ListModelsRequest) {
	return request.post<ListModelsResponse>("/list-models", params, {
		timeout: 30000,
	});
}

/**
 * 探测指定模型支持的思考深度档位（串行逐档位请求，耗时较长）
 * @param params 探测请求参数
 */
export function probeReasoning(params: ProbeReasoningRequest) {
	return request.post<ProbeReasoningResponse>("/probe-reasoning", params, {
		timeout: 120000,
	});
}

/**
 * 查询已缓存的模型思考档位（无需 API Key）
 * @param params 查询请求参数
 */
export function getModelCapability(params: GetCapabilityRequest) {
	return request.post<GetCapabilityResponse>("/model-capability", params);
}
