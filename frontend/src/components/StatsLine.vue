<script setup lang="ts">
import type { AgentType } from "@/utils/enum";
import type { Message } from "@/utils/response";
import { computed } from "vue";

// ---- Props ----

const props = defineProps<{
	messages: Message[];
}>();

// ---- 聚合 ----

interface AgentStat {
	agent: AgentType;
	prompt: number;
	completion: number;
	latency: number;
}

const agentStats = computed<AgentStat[]>(() => {
	const byAgent = new Map<AgentType, AgentStat>();
	for (const msg of props.messages) {
		if (msg.msg_type !== "agent" || !msg.usage) continue;
		const stat = byAgent.get(msg.agent_type) ?? {
			agent: msg.agent_type,
			prompt: 0,
			completion: 0,
			latency: 0,
		};
		stat.prompt += msg.usage.prompt_tokens || 0;
		stat.completion += msg.usage.completion_tokens || 0;
		stat.latency += msg.usage.latency_ms || 0;
		byAgent.set(msg.agent_type, stat);
	}
	return [...byAgent.values()];
});

const total = computed(() => {
	let prompt = 0;
	let completion = 0;
	let latency = 0;
	let firstSum = 0;
	let firstCount = 0;
	for (const msg of props.messages) {
		if (msg.msg_type !== "agent" || !msg.usage) continue;
		prompt += msg.usage.prompt_tokens || 0;
		completion += msg.usage.completion_tokens || 0;
		latency += msg.usage.latency_ms || 0;
		if (msg.usage.first_token_ms) {
			firstSum += msg.usage.first_token_ms;
			firstCount += 1;
		}
	}
	return {
		prompt,
		completion,
		latency,
		avgFirst: firstCount ? Math.round(firstSum / firstCount) : 0,
		tps: latency > 0 ? completion / (latency / 1000) : 0,
	};
});

// ---- 格式化 ----

function fmtTokens(n: number): string {
	if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
	if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
	return String(n);
}

function fmtMs(ms: number): string {
	if (ms >= 60000) {
		const m = Math.floor(ms / 60000);
		return `${m}m${Math.round((ms % 60000) / 1000)}s`;
	}
	if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
	return `${ms}ms`;
}
</script>

<template>
  <div v-if="total.completion > 0 || total.prompt > 0"
    class="flex h-6 shrink-0 items-center gap-3 border-t px-4 font-mono text-[12px] tabular-nums text-muted-foreground/70 select-none">
    <span>{{ agentStats.length }} Agent</span>
    <span class="text-muted-foreground/30">|</span>
    <span>LLM {{ fmtMs(total.latency) }} · 首token {{ fmtMs(total.avgFirst) }} · {{ total.tps.toFixed(1) }} tok/s</span>
    <span class="text-muted-foreground/30">|</span>
    <span>输入 {{ fmtTokens(total.prompt) }} / 输出 {{ fmtTokens(total.completion) }} token</span>
  </div>
</template>
