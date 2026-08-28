"""枚举类型定义模块。"""

from enum import Enum


class CompTemplate(str, Enum):
    """竞赛模板类型（美赛模板已下架：无 EN 论文章节引擎，等排期后随 PDF 题目导入一并加回）。"""

    CHINA = "CHINA"


class FormatOutPut(str, Enum):
    """输出格式类型。"""
    Markdown = "Markdown"
    LaTeX = "LaTeX"


class AgentType(str, Enum):
    """Agent 类型标识。"""
    COORDINATOR = "CoordinatorAgent"
    MODELER = "ModelerAgent"
    CODER = "CoderAgent"
    WRITER = "WriterAgent"
    SYSTEM = "SystemAgent"


class AgentStatus(str, Enum):
    """Agent 执行状态。"""
    START = "start"
    WORKING = "working"
    DONE = "done"
    ERROR = "error"
    SUCCESS = "success"
