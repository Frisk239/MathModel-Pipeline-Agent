import type { AgentType } from "@/utils/enum";
import { Bot, Code2, Compass, PenLine, Sigma } from "lucide-vue-next";
import type { FunctionalComponent } from "vue";

export interface AgentMeta {
	/** lucide 图标组件（正式 UI 禁 emoji 头像） */
	icon: FunctionalComponent;
	/** 中文角色名（与右侧产物区 Tab 文案保持同一词汇表） */
	label: string;
}

export function agentMetaOf(agentType?: AgentType | string): AgentMeta {
	switch (agentType) {
		case "CoderAgent":
			return { icon: Code2, label: "代码手" };
		case "WriterAgent":
			return { icon: PenLine, label: "论文手" };
		case "ModelerAgent":
			return { icon: Sigma, label: "建模手" };
		case "CoordinatorAgent":
			return { icon: Compass, label: "协调者" };
		default:
			return { icon: Bot, label: "Agent" };
	}
}
