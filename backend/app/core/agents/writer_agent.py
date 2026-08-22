"""写作手 Agent 模块，负责基于建模结果撰写学术论文。"""

import asyncio
from app.core.agents.agent import Agent
from app.core.llm.llm import LLM
from app.core.prompts import get_writer_prompt
from app.schemas.enums import CompTemplate, FormatOutPut
from app.config.setting import ApiType
from app.tools.openalex_scholar import OpenAlexScholar
from app.tools.exa_search import ExaSearch
from app.utils.log_util import logger
from app.services.redis_manager import redis_manager
from app.schemas.response import SystemMessage, WriterMessage
import json
from app.core.functions import writer_tools, writer_tools_anthropic
from app.schemas.A2A import WriterResponse


# TODO: 并行 parallel
# TODO: 获取当前文件下的文件
# TODO: 引用cites tool


class WriterAgent(Agent):
    """写作手 Agent，基于建模和代码执行结果撰写竞赛论文。"""
    def __init__(
        self,
        task_id: str,
        model: LLM,
        comp_template: CompTemplate = CompTemplate.CHINA,
        format_output: FormatOutPut = FormatOutPut.Markdown,
        scholar: OpenAlexScholar | None = None,
        exa: ExaSearch | None = None,
        context_window: int = 128000,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        super().__init__(task_id, model, context_window, cancel_event=cancel_event)
        self.format_out_put = format_output
        self.comp_template = comp_template
        self.scholar = scholar
        self.exa = exa
        self.is_first_run = True
        self.system_prompt = get_writer_prompt(format_output)
        self.available_images: list[str] = []

    async def run(  # type: ignore[reportIncompatibleMethodOverride]
        self,
        prompt: str,
        available_images: list[str] | None = None,
        sub_title: str | None = None,
    ) -> WriterResponse:
        """
        执行写作任务
        Args:
            prompt: 写作提示
            available_images: 可用的图片相对路径列表（如 20250420-173744-9f87792c/编号_分布.png）
            sub_title: 子任务标题
        """
        logger.info(f"subtitle是:{sub_title}")

        # 根据 api_type 选择 tools 格式
        api_type = self.model.api_type
        tools = writer_tools_anthropic if api_type == ApiType.ANTHROPIC else writer_tools

        if self.is_first_run:
            self.is_first_run = False
            await self.append_chat_history(
                {"role": "system", "content": self.system_prompt}
            )

        if available_images:
            self.available_images = available_images
            image_lines = "\n".join(
                [f"- ![{img}]({img})" for img in available_images]
            )
            image_prompt = (
                f"\n\n【必须插入的图片列表】\n"
                f"以下图片是代码手生成的，你必须在论文相关段落后用 Markdown 格式逐一插入：\n"
                f"{image_lines}\n"
                f"插入格式为独占一行的 ![描述](文件名)，每张图片后需配3行以上的分析解读。\n"
            )
            logger.info(f"image_prompt是:{image_prompt}")
            prompt = prompt + image_prompt

        logger.info(f"{self.__class__.__name__}:开始:执行对话")

        await self.append_chat_history({"role": "user", "content": prompt})

        # 获取历史消息用于本次对话
        response = await self._chat(
            history=self.chat_history,
            tools=tools,
            tool_choice="auto",
            agent_name=self.__class__.__name__,
            sub_title=sub_title,
        )

        footnotes = []
        response_content: str = ""

        # 工具调用循环：模型可能连续多次检索（换关键词/换工具），直到给出正文
        max_tool_rounds = 6
        tool_rounds = 0
        while response.tool_calls and tool_rounds < max_tool_rounds:
            tool_rounds += 1
            tool_call = response.tool_calls[0]
            if tool_call.name not in ("search_papers", "search_web"):
                logger.warning(f"未知工具 {tool_call.name}，跳出工具循环")
                break

            logger.info(f"调用工具: {tool_call.name}（第{tool_rounds}轮）")
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content=f"写作手调用{tool_call.name}工具"),
            )

            query = json.loads(tool_call.arguments)["query"]

            await redis_manager.publish_message(
                self.task_id,
                WriterMessage(
                    content=query,
                ),
            )

            # 更新对话历史 - 添加助手的响应
            assistant_msg: dict = {"role": "assistant", "content": response.content}
            if response.reasoning_content:
                assistant_msg["reasoning_content"] = response.reasoning_content
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in response.tool_calls
            ]
            await self.append_chat_history(assistant_msg)

            try:
                search_str = await self._run_search(tool_call.name, query)
            except Exception as e:
                # 理论上 _run_search 内部已降级，此处兜底防止错误文案进入论文
                logger.error(f"搜索工具异常: {str(e)}")
                search_str = "搜索服务暂时不可用，请基于已有材料继续撰写本节。"
            logger.info(f"搜索结果\n{search_str}")
            await self.append_chat_history(
                {
                    "role": "tool",
                    "content": search_str,
                    "tool_call_id": tool_call.id,
                    "name": tool_call.name,
                }
            )

            response = await self._chat(
                history=self.chat_history,
                tools=tools,
                tool_choice="auto",
                agent_name=self.__class__.__name__,
                sub_title=sub_title,
            )

        if response.tool_calls:
            # 超出工具轮次上限仍在请求工具：强制直接输出，避免空章节
            logger.warning("工具调用超过轮次上限，强制要求直接输出内容")
            await self.append_chat_history(
                {
                    "role": "user",
                    "content": "工具调用次数已达上限，请立即直接输出本节内容，不要再调用任何工具。",
                }
            )
            response = await self._chat(
                history=self.chat_history,
                agent_name=self.__class__.__name__,
                sub_title=sub_title,
            )

        response_content = response.content or ""
        if not response_content:
            logger.warning(f"章节内容为空（sub_title={sub_title}），已返回占位提示")
            response_content = "（本节生成失败，请重试）"
        self.chat_history.append({"role": "assistant", "content": response_content, "reasoning_content": response.reasoning_content} if response.reasoning_content else {"role": "assistant", "content": response_content})
        logger.info(f"{self.__class__.__name__}:完成:执行对话")
        return WriterResponse(response_content=response_content, footnotes=footnotes)

    async def _run_search(self, tool_name: str, query: str) -> str:
        """执行文献/网页搜索并格式化结果。

        搜索失败时返回降级提示而不是错误文案，避免内部信息泄露进论文正文。
        """
        try:
            if tool_name == "search_web":
                assert self.exa is not None, "exa 未初始化"
                results = await self.exa.search(query)
                return self.exa.results_to_str(results)
            assert self.scholar is not None, "scholar 未初始化"
            papers = await self.scholar.search_papers(query)
            return self.scholar.papers_to_str(papers)
        except Exception as e:
            logger.error(f"搜索工具 {tool_name} 失败: {str(e)}")
            return (
                "搜索服务暂时不可用。请基于已有材料继续撰写本节内容，"
                "不要在论文正文中提及搜索失败或本条提示。"
            )

    async def summarize(self) -> str:
        """总结对话内容，生成任务执行摘要。"""
        try:
            await self.append_chat_history(
                {"role": "user", "content": "请简单总结以上完成什么任务取得什么结果:"}
            )
            # 获取历史消息用于本次对话
            response = await self._chat(
                history=self.chat_history, agent_name=self.__class__.__name__
            )
            response_content = response.content or ""
            summary_msg: dict = {"role": "assistant", "content": response_content}
            if response.reasoning_content:
                summary_msg["reasoning_content"] = response.reasoning_content
            await self.append_chat_history(summary_msg)
            return response_content
        except Exception as e:
            logger.error(f"总结生成失败: {str(e)}")
            # 返回一个基础总结，避免完全失败
            return "由于网络原因无法生成详细总结，但已完成主要任务处理。"
