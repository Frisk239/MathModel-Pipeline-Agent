"""工作流模块，编排多 Agent 协作完成数学建模任务。"""

import asyncio
from dataclasses import dataclass
from typing import Any
import time
from pathlib import Path

from app.core.agents import WriterAgent, CoderAgent, CoordinatorAgent, ModelerAgent
from app.core.task_state import TaskPhase, TaskStateMachine
from app.core.quality.contracts import GateReport, Severity
from app.core.quality.g1_data_gate import (
    check_data_completeness,
    drop_template_attachments,
    extract_required_from_problem,
)
from app.core.quality.g3_text_gate import (
    check_citation_integrity,
    check_section_text,
)
from app.schemas.request import Problem
from app.schemas.response import SystemMessage
from app.services.approval import wait_for_approval
from app.tools.openalex_scholar import OpenAlexScholar
from app.tools.exa_search import ExaSearch
from app.utils.log_util import logger
from app.utils.common_utils import create_work_dir, get_config_template
from app.models.user_output import UserOutput
from app.config.setting import settings
from app.core.quality.g2_code_gate import (
    check_notebook_artifacts,
    combine_g2,
    format_repair_roadmap,
    is_plan_fallback_candidate,
    run_g2_ai_review,
)
from app.core.quality.g4_final_review import run_g4_final_review, run_g4_recheck
from app.tools.interpreter_factory import create_interpreter
from app.tools.env_capability import get_capability_description
from app.core.quality.deliverable_hygiene import archive_stale_deliverables
from app.services.redis_manager import redis_manager
from app.tools.notebook_serializer import NotebookSerializer
from app.core.flows import Flows
from app.core.llm.llm_factory import LLMFactory

# G3 shingle 检测的内部材料源（系统降级/工具文案，逐字复制进论文即违规）
INTERNAL_SOURCE_TEXTS = [
    "搜索服务暂时不可用。请基于已有材料继续撰写本节内容，不要在论文正文中提及搜索失败或本条提示。",
    "工具调用次数已达上限，请立即直接输出本节内容，不要再调用任何工具。",
]


class WorkFlow:
    """工作流基类。"""

    def __init__(self):
        pass

    def execute(self) -> None:
        """执行工作流。"""
        # RichPrinter.workflow_start()
        # RichPrinter.workflow_end()
        pass


@dataclass
class _PipelineContext:
    """跨阶段共享的流水线上下文（_execute_impl 拆分引入）。

    字段在阶段推进中逐步填充，None 表示尚未到达对应阶段。
    """

    problem: Problem
    llm_factory: LLMFactory | None = None
    coordinator_llm: Any = None
    modeler_llm: Any = None
    coder_llm: Any = None
    writer_llm: Any = None
    review_llm: Any = None
    coordinator_agent: Any = None
    coordinator_response: Any = None
    modeler_agent: Any = None
    modeler_response: Any = None
    env_capability: str | None = None
    user_output: Any = None
    code_interpreter: Any = None
    scholar: Any = None
    exa: Any = None
    coder_agent: Any = None
    writer_agent: Any = None
    flows: Any = None
    config_template: Any = None


class MathModelWorkFlow(WorkFlow):
    """数学建模工作流，协调协调者、建模手、代码手和写作手完成完整建模任务。"""
    task_id: str  #
    work_dir: str  # worklow work dir
    ques_count: int = 0  # 问题数量
    questions: dict[str, str | int] = {}  # 问题
    cancel_event: asyncio.Event | None = None  # 取消信号
    state: TaskStateMachine  # 任务状态机
    gate_reports: list[GateReport] = []  # 全部门报告（终局落盘 verify_report.md）
    _writing_started: bool = False

    async def _check_cancelled(self) -> None:
        """检查是否收到取消信号，若已取消则发布通知并抛出 CancelledError。"""
        if self.cancel_event and self.cancel_event.is_set():
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content="任务已停止", type="warning"),
            )
            raise asyncio.CancelledError("任务被用户停止")

    async def _write_section_with_gate(
        self,
        writer_agent: WriterAgent,
        prompt: str,
        available_images: list[str] | None,
        sub_title: str,
        exempt_text: str = "",
    ):
        """写一节并过 G3 文本门禁；MATERIAL 时带路线图补丁式修复（≤3 轮）。"""
        if not self._writing_started:
            self.state.transition(TaskPhase.WRITING, note="开始写作")
            self._writing_started = True

        if not settings.QUALITY_GATES_ENABLED:
            # A/B 基线：门关闭，不做任何检查（状态转移保持在短路之前）
            return await writer_agent.run(
                prompt,
                available_images=available_images,
                sub_title=sub_title,
            )

        response = await writer_agent.run(
            prompt,
            available_images=available_images,
            sub_title=sub_title,
        )
        current_prompt = prompt

        while True:
            report = check_section_text(
                response.response_content or "",
                section_key=sub_title,
                internal_sources=INTERNAL_SOURCE_TEXTS,
                exemptions=[exempt_text] if exempt_text else [],
            )
            report.round_no = self.state.repair_used("g3")
            if report.verdict.value != "pass":
                self.gate_reports.append(report)

            if report.verdict.value != "material":
                return response

            # MATERIAL：申请修复轮次（超限转"人工"——一期落点=警告+记录后放行）
            try:
                round_no = self.state.request_repair("g3")
            except Exception:
                if settings.AUTO_MODE:
                    self.state.record_auto_degrade(
                        "g3", f"({sub_title}) {report.summary}"
                    )
                    return response
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(
                        content=(
                            f"章节 {sub_title} 的 G3 修复轮次已耗尽（3 轮），"
                            "遗留问题已记录到 verify_report.md，需人工复核。"
                        ),
                        type="warning",
                    ),
                )
                return response

            roadmap_text = "\n".join(
                f"- 问题：{it.problem}（位置：{it.evidence_anchor}；验收：{it.acceptance_criteria}）"
                for it in report.items
            )
            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(
                    content=f"章节 {sub_title} 未通过文本门禁，第 {round_no} 轮定向修复",
                    type="warning",
                ),
            )
            current_prompt = (
                f"{current_prompt}\n\n【上一稿未通过文本质量门禁，必须修复以下问题后重新输出本节】\n"
                f"{roadmap_text}\n"
                "要求：只修复上述问题，其余内容保持不变；输出完整修订后的本节内容。"
            )
            response = await writer_agent.run(
                current_prompt,
                available_images=available_images,
                sub_title=sub_title,
            )

    def _write_verify_report(self) -> None:
        """把全部门报告落盘为 verify_report.md（审计与人工复核入口）。"""
        lines = ["# 质量门报告", ""]
        if not self.gate_reports:
            lines.append("(无门报告)")
        for r in self.gate_reports:
            lines.append(f"## {r.gate.value} — {r.verdict.value}")
            lines.append(f"- 摘要：{r.summary}")
            lines.append(f"- 修复轮次：{r.round_no}")
            if r.not_checked:
                lines.append(f"- 未核查项（如实记录）：{'; '.join(r.not_checked)}")
            for it in r.items:
                lines.append(
                    f"- [{it.severity.value}/{it.obligation.value}] {it.problem}"
                    f"（验收：{it.acceptance_criteria or '—'}）"
                )
            lines.append("")
        snap = self.state.snapshot()
        lines.append("## 任务状态")
        lines.append(f"- 终态：{snap['phase']}{'（' + snap['fail_reason'] + '）' if snap['fail_reason'] else ''}")
        lines.append(f"- 修复轮次：{snap['repair_rounds']}")
        if snap["override_history"]:
            lines.append("## 人工干预记录（全量在案）")
            for r in snap["override_history"]:
                lines.append(
                    f"- [{r['checkpoint']}] {r['action']}"
                    f"{'：' + r['feedback'][:120] if r['feedback'] else ''}"
                )
        Path(self.work_dir).joinpath("verify_report.md").write_text(
            "\n".join(lines), encoding="utf-8"
        )

    # ---- 人工检查点（二期） ----

    def _checkpoint_enabled(self, name: str) -> bool:
        """检查点开关：AUTO_MODE 一票全关，其次 HIL 配置。"""
        if settings.AUTO_MODE or not getattr(settings, "HIL_ENABLED", True):
            return False
        return bool(settings.HIL_CHECKPOINTS.get(name, False))

    async def _run_checkpoint(self, checkpoint: str, payload, revise_fn=None) -> str:
        """挂起等待审批；revise 时执行返工函数并回到同一检查点，直到 approve/reject。

        Args:
            payload: 审批材料 dict 或返回 dict 的 callable（返工后刷新）。
            revise_fn: async fn(feedback: str) -> None，人工意见驱动的定向返工。
        Returns:
            最终动作（approve / reject）。
        """
        while True:
            material = payload() if callable(payload) else payload
            decision = await wait_for_approval(
                self.task_id, self.state, checkpoint, material
            )
            if decision.action == "approve":
                return "approve"
            if decision.action == "reject":
                raise asyncio.CancelledError(
                    f"人工否决（{checkpoint}）：{decision.feedback or '无附加说明'}"
                )
            # revise：人工意见驱动定向返工（不设轮次上限——人是最终决策者）
            if revise_fn is not None:
                await revise_fn(decision.feedback)

    async def _pre_review_advisory(self, review_llm, model_plan_text: str) -> str:
        """AI 预审（advisory，不阻断）：建模方案风险摘要，供检查点②参考。"""
        try:
            resp = await review_llm.chat(
                history=[
                    {
                        "role": "user",
                        "content": (
                            "你是建模方案预审参谋（advisory，不替人决策）。"
                            "用不超过 6 条要点指出以下建模方案的主要风险"
                            "（方法选择/数据使用/验证方式），每条一句话：\n\n"
                            f"{model_plan_text[:4000]}"
                        ),
                    }
                ],
                agent_name="ModelPreReview",
            )
            return (resp.content or "").strip()[:1200]
        except Exception as e:
            return f"(预审不可用：{str(e)[:60]})"

    def _append_limitations(self, leftover_items) -> None:
        """G4/修复耗尽后的遗留问题写入论文局限性章节（显式承认而非吞掉）。"""
        if not leftover_items:
            return
        lines = ["", "# 八、局限性说明", ""]
        lines.append("以下问题经自动修复与人工复核后仍未完全解决，如实列示：")
        for it in leftover_items:
            lines.append(f"- [{it.severity.value}] {it.problem}（验收判据：{it.acceptance_criteria or '—'}）")
        res_path = Path(self.work_dir) / "res.md"
        if res_path.exists():
            res_path.write_text(
                res_path.read_text(encoding="utf-8") + "\n" + "\n".join(lines) + "\n",
                encoding="utf-8",
            )

    async def execute(self, problem: Problem):  # type: ignore[reportIncompatibleMethodOverride]
        """执行数学建模工作流（状态机包装：任何异常都记录终态后上抛）。"""
        self.task_id = problem.task_id
        self.work_dir = create_work_dir(self.task_id)
        self.state = TaskStateMachine(self.task_id, self.work_dir)
        self.gate_reports = []
        self.g1_leftover_items = []
        self.g2_leftover_items = []
        # v3/P2-5：G2 耗尽时方案回退的使用记录（每问最多回退一次，防循环）
        self._g2_fallback_used: set[str] = set()
        self._writing_started = False
        try:
            await self._execute_impl(problem)
        except asyncio.CancelledError:
            self.state.fail("任务被用户取消")
            raise
        except Exception as e:
            self.state.fail(str(e))
            self._write_verify_report()
            raise

    async def _execute_impl(self, problem: Problem) -> None:
        """工作流主体：各阶段实现见 _run_* 方法，本方法只做编排。"""
        self.state.transition(TaskPhase.SPLITTING, note="开始拆题")
        self._validate_llm_config()

        ctx = _PipelineContext(problem=problem)
        ctx.llm_factory = LLMFactory(self.task_id)
        (
            ctx.coordinator_llm,
            ctx.modeler_llm,
            ctx.coder_llm,
            ctx.writer_llm,
        ) = ctx.llm_factory.get_all_llms()
        ctx.review_llm = ctx.llm_factory.get_review_llm()

        await self._run_coordinator(ctx)
        await self._run_g1_gate(ctx)
        await self._run_split_checkpoint(ctx)
        await self._run_modeler_phase(ctx)
        await self._prepare_coding_env(ctx)
        await self._run_solution_steps(ctx)
        await self._run_write_steps(ctx)
        await self._run_final_review(ctx)

    def _validate_llm_config(self) -> None:
        """在创建 LLM 前预校验配置，避免进入 Agent 循环后才发现缺配置。"""
        missing = []
        for name, model_val, key_val in [
            ("Coordinator", settings.COORDINATOR_MODEL, settings.COORDINATOR_API_KEY),
            ("Modeler", settings.MODELER_MODEL, settings.MODELER_API_KEY),
            ("Coder", settings.CODER_MODEL, settings.CODER_API_KEY),
            ("Writer", settings.WRITER_MODEL, settings.WRITER_API_KEY),
        ]:
            if not model_val or not str(model_val).strip():
                missing.append(f"{name} 模型 ID")
            if not key_val or not str(key_val).strip():
                missing.append(f"{name} API Key")
        if missing:
            raise ValueError(f"以下配置缺失，请先在设置中填写并保存：{', '.join(missing)}")

    async def _run_coordinator(self, ctx: "_PipelineContext") -> None:
        """拆题阶段：识别意图并产出问题清单与必需附件。"""
        coordinator_agent = CoordinatorAgent(
            self.task_id, ctx.coordinator_llm,
            context_window=settings.COORDINATOR_CONTEXT_WINDOW,
            cancel_event=self.cancel_event,
        )
        ctx.coordinator_agent = coordinator_agent

        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="识别用户意图和拆解问题ing..."),
        )

        await self._check_cancelled()

        try:
            ctx.coordinator_response = await coordinator_agent.run(ctx.problem.ques_all)
            self.questions = ctx.coordinator_response.questions
            self.ques_count = ctx.coordinator_response.ques_count
        except Exception as e:
            #  非数学建模问题
            logger.error(f"CoordinatorAgent 执行失败: {e}")
            raise e

    async def _run_g1_gate(self, ctx: "_PipelineContext") -> None:
        """G1 数据完备性门：material 时 AUTO_MODE 降级 / 人工三分支。

        注意：G1 是纯本地检查（题面附件 vs 工作目录，零 LLM 调用），故
        不受 QUALITY_GATES_ENABLED 控制——关掉它只会让"演示数据论文"
        风险回归，没有收益。
        """
        self.state.transition(TaskPhase.G1_GATE, note="拆题完成，校验数据完备性")
        # 所需附件 = Coordinator 声明（先剔除题面全为模板句的附件）∪ 题面正则提取（双保险）
        required = list(
            dict.fromkeys(
                drop_template_attachments(
                    ctx.coordinator_response.required_files, ctx.problem.ques_all
                )
                + extract_required_from_problem(ctx.problem.ques_all)
            )
        )
        g1_report = check_data_completeness(required, self.work_dir)
        self.gate_reports.append(g1_report)
        if g1_report.verdict.value == "material":
            # 数据缺失只有人能判断：漏传（应终止补传）还是模板类误报（应继续）——挂起人工确认
            if settings.AUTO_MODE:
                self.state.record_auto_degrade("g1", g1_report.summary)
                self.g1_leftover_items.extend(g1_report.items)
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(
                        content=f"G1（全自动降级）：{g1_report.summary}", type="warning"
                    ),
                )
            else:
                await redis_manager.publish_message(
                    self.task_id, SystemMessage(content=g1_report.summary, type="error")
                )
                g1_decision = await wait_for_approval(
                    self.task_id,
                    self.state,
                    "g1_missing",
                    {
                        "title": "G1 数据完备性检查未通过，需人工确认",
                        "report": g1_report.summary,
                        "roadmap": "; ".join(
                            it.problem for it in g1_report.items
                        ),
                        "options": ["approve", "revise", "reject"],
                        "hint": "批准=按缺失继续（模板类误报）；否决=终止任务补传附件",
                    },
                )
                if g1_decision.action == "reject":
                    self.state.fail(f"G1 人工终止：{g1_report.summary}")
                    self._write_verify_report()
                    raise ValueError(
                        f"G1 数据完备性门未通过（人工终止）：{g1_report.summary}"
                    )
                # approve：人工确认继续（决策在案）
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(content="G1 人工确认：按现状继续", type="success"),
                )
        self.state.transition(TaskPhase.MODELING, note="数据完备，进入建模")

    async def _run_split_checkpoint(self, ctx: "_PipelineContext") -> None:
        """检查点①：拆题结果人工审批（revise 注入意见重跑拆题）。"""
        if not self._checkpoint_enabled("problem_split"):
            return

        def _ques_preview() -> str:
            qs = {
                k: v
                for k, v in self.questions.items()
                if k.startswith("ques") and k != "ques_count"
            }
            return "\n".join(
                f"{i}. {v}" for i, (_, v) in enumerate(sorted(qs.items()), 1)
            )[:2000]

        async def _split_revise(feedback: str) -> None:
            """人工意见注入 Coordinator 对话历史，重跑拆题。"""
            ctx.coordinator_agent.chat_history.clear()
            ctx.coordinator_response = await ctx.coordinator_agent.run(
                ctx.problem.ques_all
                + f"\n\n【人工审批意见，请据此调整问题拆解】\n{feedback}"
            )
            self.questions = ctx.coordinator_response.questions
            self.ques_count = ctx.coordinator_response.ques_count

        await self._run_checkpoint(
            "split_review",
            lambda: {
                "title": "问题拆解审批",
                "summary": f"共拆解 {self.ques_count} 个问题",
                "questions": _ques_preview(),
                "options": ["approve", "revise", "reject"],
            },
            revise_fn=_split_revise,
        )
        await redis_manager.publish_message(
            self.task_id, SystemMessage(content="拆题审批通过", type="success")
        )

    async def _run_modeler_phase(self, ctx: "_PipelineContext") -> None:
        """建模阶段 + 检查点②（方案审批，带 AI 预审参谋，advisory 不替人决策）。"""
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="识别用户意图和拆解问题完成,任务转交给建模手"),
        )
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="建模手开始建模ing..."),
        )
        await self._check_cancelled()

        modeler_agent = ModelerAgent(
            self.task_id, ctx.modeler_llm,
            context_window=settings.MODELER_CONTEXT_WINDOW,
            cancel_event=self.cancel_event,
        )
        ctx.modeler_agent = modeler_agent

        # v3/F2：注入实测环境能力清单，方案承诺的求解器必须限制在清单内（灭求解器幻觉）
        ctx.env_capability = get_capability_description(
            "remote" if settings.E2B_API_KEY else "local"
        )

        ctx.modeler_response = await modeler_agent.run(
            ctx.coordinator_response, env_capability=ctx.env_capability
        )

        if not self._checkpoint_enabled("model_selection"):
            return
        import json as _json

        def _plan_text() -> str:
            return _json.dumps(
                getattr(ctx.modeler_response, "questions_solution", {}),
                ensure_ascii=False,
            )[:4000]

        advisory = await self._pre_review_advisory(ctx.review_llm, _plan_text())

        async def _model_revise(feedback: str) -> None:
            await ctx.modeler_agent.append_chat_history(
                {
                    "role": "user",
                    "content": f"【人工审批意见，请据此修订建模方案】\n{feedback}",
                }
            )
            ctx.modeler_response = await ctx.modeler_agent.run(
                ctx.coordinator_response, env_capability=ctx.env_capability
            )

        await self._run_checkpoint(
            "model_review",
            lambda: {
                "title": "建模方案审批",
                "plan": _plan_text(),
                "ai_advisory": advisory,
                "options": ["approve", "revise", "reject"],
            },
            revise_fn=_model_revise,
        )

    async def _prepare_coding_env(self, ctx: "_PipelineContext") -> None:
        """创建编码/写作所需的运行环境：解释器、检索、Agent 实例、Flows。"""
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="正在创建代码沙盒环境"),
        )
        ctx.user_output = UserOutput(work_dir=self.work_dir, ques_count=self.ques_count)

        notebook_serializer = NotebookSerializer(work_dir=self.work_dir)
        ctx.code_interpreter = await create_interpreter(
            kind="local",
            task_id=self.task_id,
            work_dir=self.work_dir,
            notebook_serializer=notebook_serializer,
            timeout=3000,
        )

        # OpenAlex 未配置邮箱时不阻断任务，Writer 搜索时降级
        ctx.scholar = None
        if settings.OPENALEX_EMAIL:
            ctx.scholar = OpenAlexScholar(
                task_id=self.task_id,
                email=settings.OPENALEX_EMAIL,
                api_key=settings.OPENALEX_API_KEY,
            )
        else:
            logger.warning("未配置 OPENALEX_EMAIL，论文手文献检索将降级")

        ctx.exa = ExaSearch(api_key=settings.EXA_API_KEY) if settings.EXA_API_KEY else None
        if ctx.exa is None:
            logger.warning("未配置 EXA_API_KEY，论文手语义搜索将不可用")

        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="创建完成"),
        )
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(content="初始化代码手"),
        )
        self.state.transition(TaskPhase.CODING, note="进入求解循环")

        ctx.coder_agent = CoderAgent(
            task_id=ctx.problem.task_id,
            model=ctx.coder_llm,
            work_dir=self.work_dir,
            max_chat_turns=settings.MAX_CHAT_TURNS,
            max_retries=settings.MAX_RETRIES,
            code_interpreter=ctx.code_interpreter,
            context_window=settings.CODER_CONTEXT_WINDOW,
            cancel_event=self.cancel_event,
        )

        ctx.writer_agent = WriterAgent(
            task_id=ctx.problem.task_id,
            model=ctx.writer_llm,
            comp_template=ctx.problem.comp_template,
            format_output=ctx.problem.format_output,
            scholar=ctx.scholar,
            exa=ctx.exa,
            context_window=settings.WRITER_CONTEXT_WINDOW,
            cancel_event=self.cancel_event,
        )

        ctx.flows = Flows(self.questions, env_capability=ctx.env_capability)
        ctx.config_template = get_config_template(ctx.problem.comp_template)

    async def _run_solution_steps(self, ctx: "_PipelineContext") -> None:
        """逐问求解：Coder 编码 + G2 质量门修复回路 + Writer 写对应章节。"""
        solution_flows = ctx.flows.get_solution_flows(
            self.questions, ctx.modeler_response
        )

        for key, value in solution_flows.items():
            await self._check_cancelled()

            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content=f"代码手开始求解{key}"),
            )

            coder_prompt = value["coder_prompt"]
            subtask_started_at = time.time()  # v3/P2-3：本问交付物溯源与归档的时间基准
            coder_response = await ctx.coder_agent.run(
                prompt=coder_prompt, subtask_title=key
            )

            coder_response = await self._run_g2_repair_loop(
                ctx, key, coder_prompt, subtask_started_at, coder_response
            )

            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content=f"代码手求解成功{key}", type="success"),
            )

            writer_prompt = ctx.flows.get_writer_prompt(
                key,
                coder_response.code_response or "",
                ctx.code_interpreter,
                ctx.config_template,
            )

            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content=f"论文手开始写{key}部分"),
            )

            ## TODO: 图片引用错误
            writer_response = await self._write_section_with_gate(
                ctx.writer_agent,
                prompt=writer_prompt,
                available_images=coder_response.created_images,
                sub_title=key,
                exempt_text=ctx.problem.ques_all,
            )

            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content=f"论文手完成{key}部分"),
            )

            ctx.user_output.set_res(key, writer_response)

        # 关闭沙盒
        await ctx.code_interpreter.cleanup()
        logger.info(ctx.user_output.get_res())

    async def _run_g2_repair_loop(
        self,
        ctx: "_PipelineContext",
        key: str,
        coder_prompt: str,
        subtask_started_at: float,
        coder_response,
    ):
        """G2 代码质量门：L1 脚本 + L2 AI 评审，MATERIAL 定向修复（≤3 轮）。

        含 P2-4 分级修复指令、P2-3 交付物归档、P2-5 方案回退、AUTO_MODE
        降级放行与人工三分支。
        """
        import os as _os

        import json as _json2

        model_plan_text = _json2.dumps(
            getattr(ctx.modeler_response, "questions_solution", {}),
            ensure_ascii=False,
        )
        nb_path = _os.path.join(self.work_dir, "notebook.ipynb")
        g2_prior_items: list[str] | None = None
        while settings.QUALITY_GATES_ENABLED:
            l1_items = check_notebook_artifacts(
                nb_path,
                self.work_dir,
                coder_response.created_images,
                deliverable_since=subtask_started_at,
            )
            l2_report = await run_g2_ai_review(
                ctx.review_llm,
                nb_path,
                model_plan_text,
                coder_response.code_response or "",
                ctx.problem.ques_all,
                prior_items=g2_prior_items,
            )
            g2_report = combine_g2(l1_items, l2_report)
            g2_prior_items = [it.problem for it in g2_report.items]
            g2_report.round_no = self.state.repair_used("g2")
            self.gate_reports.append(g2_report)
            if g2_report.verdict.value != "material":
                break

            # v3/P2-4：分级聚焦的修复指令（critical/major 必须解决，minor 明示延后）
            roadmap_text = format_repair_roadmap(g2_report.items)
            # 报错 cell 类问题给出定向清理指令（重跑全任务不会清掉旧 cell）
            if any("报错输出" in it.problem or "notebook cell" in it.problem for it in g2_report.items):
                roadmap_text += (
                    "\n\n【报错 cell 专项指令】以上报错 cell 必须逐个处理："
                    "定位对应 cell，修复其代码或直接删除该 cell 后重跑，"
                    "禁止保留报错的历史尝试 cell；其余已成功的代码保持不变。"
                )
            try:
                round_no = self.state.request_repair(
                    "g2", cap=settings.G2_MAX_REPAIR_ROUNDS
                )
            except Exception:
                # v3/P2-5：修复轮耗尽 → 先试方案回退（+1 轮），否则降级/人工决策
                fallback = await self._g2_plan_fallback(
                    ctx, key, g2_report, roadmap_text, subtask_started_at
                )
                if fallback is not None:
                    round_no, roadmap_text = fallback
                    coder_prompt = f"{coder_prompt}\n\n{roadmap_text}"
                    coder_response = await ctx.coder_agent.run(
                        prompt=coder_prompt, subtask_title=key
                    )
                    continue
                action, extra = await self._g2_exhausted_decision(
                    key, g2_report, roadmap_text
                )
                if action == "break":
                    break
                round_no = self.state.repair_used("g2")
                roadmap_text += extra

            # v3/P2-3：归档本问上轮交付物，防止新一轮读取遗留产物冒充本轮产出
            # （只动 mtime>=subtask_started_at 的本问产物，此前问次的合法产物不动）
            archived = archive_stale_deliverables(
                self.work_dir, round_no, since=subtask_started_at
            )
            if archived:
                roadmap_text += (
                    "\n\n【交付物溯源指令】上一轮交付物（"
                    f"{'、'.join(archived[:5])}）已归档至 "
                    f"_retry_archive/round{round_no}/（仅供审计，禁止读取）。"
                    "本轮必须由 notebook 代码端到端重新生成全部结果文件，"
                    "禁止把读取到的既有文件内容当作本轮求解结果。"
                )

            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(
                    content=f"G2 门拦截（{key}），第 {round_no} 轮定向修复",
                    type="warning",
                ),
            )
            coder_prompt = (
                f"{coder_prompt}\n\n【上一轮代码未通过质量门，必须修复以下问题】\n"
                f"{roadmap_text}\n"
                "要求：修复上述问题后重新求解本问。"
            )
            coder_response = await ctx.coder_agent.run(
                prompt=coder_prompt, subtask_title=key
            )
        return coder_response

    async def _g2_plan_fallback(
        self,
        ctx: "_PipelineContext",
        key: str,
        g2_report,
        roadmap_text: str,
        subtask_started_at: float,
    ) -> tuple[int, str] | None:
        """P2-5 方案回退：主因为"方案未实现"（方案过重）且该问未回退过时，
        回退第二候选给 +1 轮，优于带一串 critical 落款降级放行。

        仅 AUTO_MODE 生效：人工模式把决策权留给人（revise 可写"改用候选2"）。
        不满足回退条件返回 None，由调用方走降级/人工分支。
        """
        if not (
            settings.AUTO_MODE
            and key not in self._g2_fallback_used
            and is_plan_fallback_candidate(g2_report.items)
        ):
            return None
        self._g2_fallback_used.add(key)
        self.state.repair_rounds["g2"] = self.state.repair_used("g2") + 1
        self.state.save()
        round_no = self.state.repair_used("g2")
        self.state.record_auto_degrade(
            "g2_plan_fallback",
            f"({key}) 方案超出实现预算（{g2_report.summary}），"
            "自动回退建模候选矩阵的第二候选并追加一轮",
        )
        archived_fb = archive_stale_deliverables(
            self.work_dir, round_no, since=subtask_started_at
        )
        plan_excerpt = str(
            ctx.modeler_response.questions_solution.get(key, "")
        )[:1500]
        fallback_text = (
            "【方案回退指令】原方案在修复轮耗尽后仍无法实现"
            f"（门报告：{g2_report.summary}）。判定主因：方案复杂度"
            "超出实现预算。要求改用建模方案候选矩阵中的第二候选模型"
            "（或更简单的可执行候选）：\n"
            "1. 先用最简结构跑通主链路：核心决策变量→目标→关键约束→"
            "求解→结果文件写出\n"
            "2. 原方案中预算内无法实现的复杂结构（如大M半连续、复杂"
            "滚动窗口），要么给出等价简化实现，要么在执行总结中如实"
            "记录差异与理由\n"
            "3. 环境能力清单仍然有效，求解器只能用清单内可用项\n"
            f"【原方案节选】{plan_excerpt}"
        )
        if archived_fb:
            fallback_text += (
                "\n（上轮交付物已归档至 _retry_archive/，禁止读取，"
                "本轮必须端到端重新生成）"
            )
        await redis_manager.publish_message(
            self.task_id,
            SystemMessage(
                content=f"({key}) 方案超预算，自动回退第二候选（+1 轮）",
                type="warning",
            ),
        )
        return round_no, fallback_text

    async def _g2_exhausted_decision(
        self, key: str, g2_report, roadmap_text: str
    ) -> tuple[str, str]:
        """修复轮耗尽处置：AUTO_MODE 降级放行，或人工三分支。

        返回 ("break", "") 放行；("continue", 附加指令) 授权追加一轮；
        reject 直接抛 CancelledError 中止任务。
        """
        if settings.AUTO_MODE:
            if not any(it.severity == Severity.CRITICAL for it in g2_report.items):
                # 分级放行：耗尽后仅剩 major/minor（A/B 实测常态），遗留照记
                # 局限性章节，但不记 auto_degraded 降级审计
                self.g2_leftover_items.extend(g2_report.items)
                logger.info(f"({key}) 修复轮耗尽且无 critical，分级放行")
                return "break", ""
            # 全自动模式：耗尽自动降级放行（遗留如实记录，与人工决策区分审计）
            self.state.record_auto_degrade("g2", f"({key}) {g2_report.summary}")
            self.g2_leftover_items.extend(g2_report.items)
            return "break", ""
        # 轮次耗尽 → 人工三选一（决策记录在案）
        # 动作词表与检查点统一：approve=放行 / revise=带意见追加轮 / reject=中止
        decision = await wait_for_approval(
            self.task_id,
            self.state,
            "g2_exhausted",
            {
                "title": f"G2 修复轮次耗尽（{key}）",
                "report": g2_report.summary,
                "roadmap": roadmap_text[:2000],
                "options": ["approve", "revise", "reject"],
            },
        )
        if decision.action == "approve":
            # 人工带问题放行：G2 遗留同样写入论文局限性章节（不静默吞掉）
            self.g2_leftover_items.extend(g2_report.items)
            return "break", ""
        if decision.action == "reject":
            raise asyncio.CancelledError("人工中止（G2 轮次耗尽）") from None
        # revise：人工授权追加一轮（记录绕过 cap 的授权）
        self.state.repair_rounds["g2"] = self.state.repair_used("g2") + 1
        self.state.save()
        extra = f"\n【人工补充意见】{decision.feedback}" if decision.feedback else ""
        return "continue", extra

    async def _run_write_steps(self, ctx: "_PipelineContext") -> None:
        """write steps：结论性章节（摘要/评价等）写作。"""
        write_flows = ctx.flows.get_write_flows(
            ctx.user_output, ctx.config_template, ctx.problem.ques_all
        )
        for key, value in write_flows.items():
            await self._check_cancelled()

            await redis_manager.publish_message(
                self.task_id,
                SystemMessage(content=f"论文手开始写{key}部分"),
            )

            writer_response = await self._write_section_with_gate(
                ctx.writer_agent,
                prompt=value,
                available_images=None,
                sub_title=key,
                exempt_text=ctx.problem.ques_all,
            )

            ctx.user_output.set_res(key, writer_response)

        logger.info(ctx.user_output.get_res())

    async def _run_final_review(self, ctx: "_PipelineContext") -> None:
        """组装、G3 引用完整性、G4 终审与复核、检查点④、遗留落局限性。"""
        import os as _os

        self.state.transition(TaskPhase.ASSEMBLING, note="论文组装")
        ctx.user_output.save_result()

        res_md = Path(self.work_dir) / "res.md"
        if res_md.exists() and settings.QUALITY_GATES_ENABLED:
            citation_report = check_citation_integrity(
                res_md.read_text(encoding="utf-8")
            )
            citation_report.round_no = self.state.repair_used("g3")
            self.gate_reports.append(citation_report)
            if citation_report.verdict.value != "pass":
                await redis_manager.publish_message(
                    self.task_id,
                    SystemMessage(
                        content=f"引用完整性检查：{citation_report.summary}", type="warning"
                    ),
                )

        # G4 终审（七维评审 + 机械裁决 + 判官披露）；A/B 基线时跳过
        if not settings.QUALITY_GATES_ENABLED:
            self.state.transition(TaskPhase.COMPLETED, note="A/B 基线：门关闭，直接完成")
            self._write_verify_report()
            return
        self.state.transition(TaskPhase.FINAL_REVIEW, note="G4 终审")
        same_family = (
            settings.REVIEW_MODEL is None or settings.REVIEW_MODEL == settings.COORDINATOR_MODEL
        )
        paper_text = res_md.read_text(encoding="utf-8") if res_md.exists() else ""
        # 参数表覆盖检查的输入：建模蓝图全文（各问方案拼接；v4 2-2）
        model_plan_text = ""
        if ctx.modeler_response is not None:
            model_plan_text = "\n".join(
                str(v)
                for v in (getattr(ctx.modeler_response, "questions_solution", None) or {}).values()
            )
        g4_report = await run_g4_final_review(
            ctx.review_llm, paper_text, same_family, model_plan_text=model_plan_text
        )
        self.gate_reports.append(g4_report)
        leftover_items = []

        if g4_report.verdict.value == "material":
            # MATERIAL：升级检查点④人工决策（务实方案：人工驱动定向重写 + 路线图复核）
            roadmap_items = g4_report.items
            g4_passed = False

            async def _paper_revise(feedback: str) -> None:
                """人工意见定向重写评价章并重组装（Writer 共享对话上下文）。"""
                await ctx.writer_agent.append_chat_history(
                    {
                        "role": "user",
                        "content": (
                            "【人工终稿审批意见，请据此重写《模型的评价、改进与推广》章节】\n"
                            f"{feedback}\n"
                            "要求：回应意见中的每个问题，输出完整的第七章内容。"
                        ),
                    }
                )
                resp = await ctx.writer_agent.run(
                    "基于人工终稿意见重写第七章（模型的评价、改进与推广），输出完整章节。",
                    sub_title="judge",
                )
                from app.schemas.A2A import WriterResponse as _WR

                ctx.user_output.set_res(
                    "judge", _WR(response_content=resp.response_content, footnotes=[])
                )
                ctx.user_output.save_result()

            while not g4_passed:
                if settings.AUTO_MODE:
                    # 全自动模式：G4 未过自动降级（遗留写局限性），不挂起
                    self.state.record_auto_degrade(
                        "g4", g4_report.summary
                    )
                    leftover_items = roadmap_items
                    g4_passed = True
                    break
                decision = await wait_for_approval(
                    self.task_id,
                    self.state,
                    "paper_review",
                    {
                        "title": "终稿审批（G4 终审未通过，需人工决策）",
                        "g4_report": g4_report.summary,
                        "items": [
                            f"[{it.severity.value}] {it.problem}" for it in roadmap_items
                        ][:12],
                        "options": ["approve", "revise", "reject"],
                    },
                )
                if decision.action == "reject":
                    raise asyncio.CancelledError(
                        f"人工否决（终稿）：{decision.feedback or '无附加说明'}"
                    )
                if decision.action == "approve":
                    leftover_items = roadmap_items  # 人工带问题放行 → 写入局限性
                    break
                await _paper_revise(decision.feedback)
                new_text = res_md.read_text(encoding="utf-8") if res_md.exists() else ""
                recheck = await run_g4_recheck(
                    ctx.review_llm, roadmap_items, new_text, paper_text
                )
                self.gate_reports.append(recheck)
                await redis_manager.publish_message(
                    self.task_id, SystemMessage(content=f"G4 复核：{recheck.summary}")
                )
                if recheck.verdict.value != "material":
                    g4_passed = True
                paper_text = new_text
        elif g4_report.verdict.value == "minor":
            leftover_items = g4_report.items  # Minor 遗留如实记录

        # 检查点④（终稿预览审批；G4 MATERIAL 场景已在上方循环内审批）
        if self._checkpoint_enabled("paper_review") and g4_report.verdict.value != "material":
            await self._run_checkpoint(
                "paper_review",
                {
                    "title": "终稿审批（G4 终审通过）",
                    "g4_report": g4_report.summary,
                    "paper_preview": paper_text[:3000],
                    "options": ["approve", "revise", "reject"],
                },
            )

        self.state.transition(TaskPhase.COMPLETED, note="流程完成")
        # G2 遗留在写入前按终态复核：L1 重跑剔除已修复项（耗尽时快照可能已过时）
        g2_leftover_final = list(self.g2_leftover_items)
        if g2_leftover_final:
            l1_still = {
                it.problem
                for it in check_notebook_artifacts(
                    _os.path.join(self.work_dir, "notebook.ipynb"), self.work_dir, None
                )
            }
            g2_leftover_final = [
                it
                for it in g2_leftover_final
                if "notebook" not in it.problem[:8] or any(frag in it.problem for frag in l1_still)
            ]
        self._append_limitations(list(leftover_items) + self.g1_leftover_items + g2_leftover_final)
        self._write_verify_report()
        # 局限性追加后重转 docx 保持一致
        if leftover_items:
            from app.utils.common_utils import md_2_docx

            md_2_docx(self.task_id)

        # 在创建 LLM 前预校验配置，避免进入 Agent 循环后才发现缺配置
