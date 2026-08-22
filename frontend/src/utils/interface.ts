import type { OutputItem } from "./response";

/** 代码单元格类型 */
export interface CodeCell {
	type: "code";
	content: string;
}

/** 结果单元格类型 */
export interface ResultCell {
	type: "result";
	code_results: OutputItem[];
}

/** 笔记本单元格类型（代码或结果） */
export type NoteCell = CodeCell | ResultCell;

/** 模型配置 */
export interface ModelConfig {
	apiKey: string;
	baseUrl: string;
	modelId: string;
	apiType: string;
	/** 上下文窗口大小（token），用于记忆压缩阈值 */
	contextWindow?: number;
	/** 思考深度档位（off/minimal/low/medium/high/max/xhigh），留空使用服务商默认 */
	reasoningEffort?: string;
	/** 思考 token 预算（仅 anthropic 协议生效） */
	thinkingBudget?: number | null;
}
