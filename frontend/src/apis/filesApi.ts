import request from "@/utils/request";

/** 工作区文件条目（与后端 /files 返回结构一致） */
export interface WorkspaceFile {
	filename: string;
	file_type: string;
}

/**
 * 获取任务工作区文件列表
 * @param task_id 任务ID
 */
export function getFiles(task_id: string) {
	return request.get<WorkspaceFile[]>("/files", {
		params: { task_id },
	});
}

/**
 * 获取单个文件下载链接
 * @param task_id 任务ID
 * @param filename 文件名
 */
export async function getFileDownloadUrl(task_id: string, filename: string) {
	return await request.get<{ download_url: string }>("/download_url", {
		params: {
			task_id,
			filename,
		},
	});
}

/**
 * 获取所有文件压缩包下载链接
 * @param task_id 任务ID
 */
export async function getAllFilesDownloadUrl(task_id: string) {
	return await request.get<{ download_url: string }>("/download_all_url", {
		params: {
			task_id,
		},
	});
}
