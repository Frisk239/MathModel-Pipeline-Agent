import request from "@/utils/request";

/** 挂起的审批材料 */
export interface PendingApproval {
	pending: boolean;
	checkpoint?: string;
	payload?: {
		title?: string;
		summary?: string;
		plan?: string;
		questions?: string;
		g4_report?: string;
		items?: string[];
		paper_preview?: string;
		ai_advisory?: string;
		options?: string[];
		revision_note?: string;
	};
}

/** 获取当前任务的挂起审批材料 */
export function getPendingApproval(taskId: string) {
	return request.get<PendingApproval>(`/approval/${taskId}`);
}

/** 提交审批决策（approve / revise / reject） */
export function submitApproval(
	taskId: string,
	action: "approve" | "revise" | "reject",
	feedback = "",
) {
	return request.post<{ success: boolean; message: string }>(
		`/approval/${taskId}`,
		{ action, feedback },
	);
}
