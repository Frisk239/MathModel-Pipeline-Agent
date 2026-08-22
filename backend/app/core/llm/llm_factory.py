"""LLM 工厂模块，根据配置创建各 Agent 使用的 LLM 实例。"""

from app.config.setting import settings
from app.core.llm.llm import LLM


class LLMFactory:
    """LLM 工厂类，根据配置创建协调者、建模手、代码手和写作手的 LLM 实例。"""

    task_id: str

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    def get_all_llms(self) -> tuple[LLM, LLM, LLM, LLM]:
        """创建所有 Agent 的 LLM 实例。

        Returns:
            包含 (coordinator_llm, modeler_llm, coder_llm, writer_llm) 的元组。
        """
        coordinator_llm = LLM(
            api_type=settings.COORDINATOR_API_TYPE,
            api_key=settings.COORDINATOR_API_KEY,
            model=settings.COORDINATOR_MODEL,
            base_url=settings.COORDINATOR_BASE_URL,
            task_id=self.task_id,
            max_tokens=settings.COORDINATOR_MAX_TOKENS,
            reasoning_effort=settings.COORDINATOR_REASONING_EFFORT,
            thinking_budget=settings.COORDINATOR_THINKING_BUDGET,
        )

        modeler_llm = LLM(
            api_type=settings.MODELER_API_TYPE,
            api_key=settings.MODELER_API_KEY,
            model=settings.MODELER_MODEL,
            base_url=settings.MODELER_BASE_URL,
            task_id=self.task_id,
            max_tokens=settings.MODELER_MAX_TOKENS,
            reasoning_effort=settings.MODELER_REASONING_EFFORT,
            thinking_budget=settings.MODELER_THINKING_BUDGET,
        )

        coder_llm = LLM(
            api_type=settings.CODER_API_TYPE,
            api_key=settings.CODER_API_KEY,
            model=settings.CODER_MODEL,
            base_url=settings.CODER_BASE_URL,
            task_id=self.task_id,
            max_tokens=settings.CODER_MAX_TOKENS,
            reasoning_effort=settings.CODER_REASONING_EFFORT,
            thinking_budget=settings.CODER_THINKING_BUDGET,
        )

        writer_llm = LLM(
            api_type=settings.WRITER_API_TYPE,
            api_key=settings.WRITER_API_KEY,
            model=settings.WRITER_MODEL,
            base_url=settings.WRITER_BASE_URL,
            task_id=self.task_id,
            max_tokens=settings.WRITER_MAX_TOKENS,
            reasoning_effort=settings.WRITER_REASONING_EFFORT,
            thinking_budget=settings.WRITER_THINKING_BUDGET,
        )

        return coordinator_llm, modeler_llm, coder_llm, writer_llm

    def get_review_llm(self) -> LLM:
        """评审模型：REVIEW_* 优先，缺省回退 Coordinator 配置。"""
        return LLM(
            api_type=settings.REVIEW_API_TYPE or settings.COORDINATOR_API_TYPE,
            api_key=settings.REVIEW_API_KEY or settings.COORDINATOR_API_KEY,
            model=settings.REVIEW_MODEL or settings.COORDINATOR_MODEL,
            base_url=settings.REVIEW_BASE_URL or settings.COORDINATOR_BASE_URL,
            task_id=self.task_id,
            max_tokens=settings.REVIEW_MAX_TOKENS or settings.COORDINATOR_MAX_TOKENS,
        )
