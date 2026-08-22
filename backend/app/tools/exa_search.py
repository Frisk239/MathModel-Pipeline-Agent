"""Exa 语义搜索模块，作为 OpenAlex 学术检索的补充引擎。

Exa 擅长中英文语义检索与最新技术资料（https://api.exa.ai），
返回网页与论文混合结果，供论文手补充 OpenAlex 覆盖不到的资料。
"""

import requests
from typing import Any
from app.utils.log_util import logger


class ExaSearch:
    """Exa 语义搜索客户端。"""

    def __init__(self, api_key: str | None = None):
        self.search_url = "https://api.exa.ai/search"
        self.api_key = api_key

    async def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        """语义搜索，返回结果列表。

        Args:
            query: 搜索关键词。
            limit: 最大返回结果数。

        Returns:
            包含 title/url/author/publishedDate/text 的字典列表。
        """
        if not self.api_key:
            raise ValueError("未配置 EXA_API_KEY")

        payload = {
            "query": query,
            "numResults": limit,
            "type": "auto",
            "contents": {"text": {"maxCharacters": 1000}},
        }
        headers = {"x-api-key": self.api_key}

        try:
            response = requests.post(
                self.search_url, json=payload, headers=headers, timeout=30
            )
            response.raise_for_status()
            return response.json().get("results", [])
        except Exception as e:
            logger.error(f"Exa 搜索请求失败: {e}")
            raise

    def results_to_str(self, results: list[dict[str, Any]]) -> str:
        """将结果列表格式化为供 LLM 消费的文本。"""
        if not results:
            return "未搜索到相关结果。"

        lines = []
        for i, r in enumerate(results, 1):
            title = r.get("title") or "无标题"
            url = r.get("url") or ""
            author = r.get("author") or "未知"
            date = (r.get("publishedDate") or "未知")[:10]
            text = (r.get("text") or "").replace("\n", " ")[:300]
            lines.append(
                f"[{i}] {title}\n作者: {author} | 发布: {date}\n链接: {url}\n摘要: {text}"
            )
        return "\n\n".join(lines)
