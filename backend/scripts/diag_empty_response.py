"""诊断 GLM anthropic 兼容端空响应（content=None）根因。

复刻 ModelerAgent 的真实请求（同系统提示词/同 questions/同环境清单注入/
同 max_tokens 与 thinking budget），打印 stop_reason、usage、各 content block
的类型与长度，用于区分：思考耗尽输出预算（stop_reason=max_tokens 且无 text）
还是端点偶发空正文（stop_reason=end_turn 且无 text）。

用法：cd backend && python scripts/diag_empty_response.py
"""

import asyncio
import json

from anthropic import AsyncAnthropic

from app.config.setting import settings
from app.core.prompts import MODELER_PROMPT
from app.tools.env_capability import get_capability_description

# 2024-C 真实拆题结果（取自任务 20260824-104931 日志，字段有截断精简）
QUESTIONS = {
    "title": "C 题 农作物的种植策略",
    "background": "某乡村地处华北山区，常年温度偏低，大多数耕地每年只能种植一季农作物。该乡村现有露天耕地 1201 亩，分散为 34 个大小不同的地块，包括平旱地、梯田、山坡地和水浇地 4 种类型。平旱地、梯田和山坡地适宜每年种植一季粮食类作物；水浇地适宜每年种植一季水稻或两季蔬菜。该乡村另有 16 个普通大棚和 4 个智慧大棚，每个大棚耕地面积为 0.6 亩。普通大棚适宜每年种植一季蔬菜和一季食用菌，智慧大棚适宜每年种植两季蔬菜。同一地块（含大棚）每季可以合种不同的作物。详见附件 1。……2023 年的农作物种植和相关统计数据见附件 2。请建立数学模型，研究下列问题：",
    "ques_count": 3,
    "ques1": "问题 1 假定各种农作物未来的预期销售量、种植成本、亩产量和销售价格相对于 2023 年保持稳定，每季种植的农作物在当季销售。如果某种作物每季的总产量超过相应的预期销售量，超过部分不能正常销售。请针对以下两种情况，分别给出该乡村 2024~2030 年农作物的最优种植方案，将结果分别填入 result1_1.xlsx 和 result1_2.xlsx 中（模板文件见附件 3）。(1) 超过部分滞销，造成浪费；(2) 超过部分按 2023 年销售价格的 50%降价出售。",
    "ques2": "问题 2 根据经验，小麦和玉米未来的预期销售量有增长的趋势，平均年增长率介于5%~10%之间，其他农作物未来每年的预期销售量相对于 2023 年大约有±5%的变化。……请综合考虑各种农作物的预期销售量、亩产量、种植成本和销售价格的不确定性以及潜在的种植风险，给出该乡村 2024~2030 年农作物的最优种植方案，将结果填入 result2.xlsx 中（模板文件见附件 3）。",
    "ques3": "问题 3 在现实生活中，各种农作物之间可能存在一定的可替代性和互补性，预期销售量与销售价格、种植成本之间也存在一定的相关性。请在问题 2 的基础上综合考虑相关因素，给出该乡村 2024~2030 年农作物的最优种植策略，通过模拟数据进行求解，并与问题 2 的结果作比较分析。",
    "required_files": ["附件1", "附件2"],
}


async def main() -> None:
    client = AsyncAnthropic(
        api_key=settings.MODELER_API_KEY,
        base_url=settings.MODELER_BASE_URL,
    )
    messages = [
        {"role": "user", "content": json.dumps(QUESTIONS, ensure_ascii=False)},
        {"role": "user", "content": get_capability_description("local")},
    ]
    print(
        f"model={settings.MODELER_MODEL} max_tokens={settings.MODELER_MAX_TOKENS} "
        f"thinking_budget={settings.MODELER_THINKING_BUDGET} "
        f"reasoning_effort={settings.MODELER_REASONING_EFFORT}"
    )
    async with client.messages.stream(
        model=settings.MODELER_MODEL,
        system=MODELER_PROMPT,
        messages=messages,
        max_tokens=65536,
        thinking={
            "type": "enabled",
            "budget_tokens": settings.MODELER_THINKING_BUDGET,
        },
    ) as stream:
        resp = await stream.get_final_message()
    print("stop_reason:", resp.stop_reason)
    print("usage:", resp.usage)
    total_text = 0
    for i, b in enumerate(resp.content):
        text = getattr(b, "text", None) or getattr(b, "thinking", None) or ""
        total_text += len(text or "")
        head = (text or "")[:100].replace("\n", "\\n")
        print(f"block[{i}] type={b.type} len={len(text or '')} head={head!r}")
    print(f"text_total={total_text}")


if __name__ == "__main__":
    asyncio.run(main())
