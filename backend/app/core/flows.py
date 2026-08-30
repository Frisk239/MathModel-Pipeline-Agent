"""工作流程定义模块，管理建模任务的求解和写作流程。"""

from app.models.user_output import UserOutput
from app.tools.base_interpreter import BaseCodeInterpreter
from app.core.agents.modeler_agent import ModelerToCoder

# v3/P2-2 数据与主链路纪律：灭 F1 清洗泥潭（修复轮全耗在 KeyError/AttributeError/
# FileNotFoundError 往返，始终未抵达建模）。规则落任务级 prompt 才可靠（三期经验）。
DATA_DISCIPLINE = """【数据与主链路纪律（必须遵守）】
1. 读完原始数据后的第一个代码 cell：将所有 DataFrame 列名统一重命名为合法标识符
（仅字母/数字/下划线，用英文语义，禁止「/」、空格、含中文单位的后缀如「亩」「元」），
并打印重命名对照表；此后全程只使用规范列名，杜绝 KeyError 与 itertuples 属性错误
2. 数据清洗与建模分离：清洗 cell 一次成型，禁止在建模阶段回头修改清洗逻辑；
确需修正时以新 cell 显式重定义并注明原因
3. 中间产物（清洗后的表）必须先落盘、再在后续 cell 读取，写盘与读盘顺序不得颠倒
4. 交付主链路优先级：建模→求解→结果文件写出→图表。必须先跑通主链路；
清洗细节、图表美化等收尾工作放最后处理"""


class Flows:
    """管理数学建模任务的求解流程和写作流程。"""
    def __init__(
        self,
        questions: dict[str, str | int],
        env_capability: str | None = None,
    ):
        self.flows: dict[str, dict] = {}
        self.questions: dict[str, str | int] = questions
        # v3/F2：执行环境能力清单，拼入各 coder_prompt，代码只能使用清单内可用库
        self.env_capability = (env_capability or "").strip()

    def set_flows(self, ques_count: int):
        """根据问题数量设置流程节点。

        Args:
            ques_count: 问题数量。
        """
        ques_str = [f"ques{i}" for i in range(1, ques_count + 1)]
        seq = [
            "firstPage",
            "RepeatQues",
            "analysisQues",
            "modelAssumption",
            "symbol",
            "eda",
            *ques_str,
            "sensitivity_analysis",
            "judge",
        ]
        self.flows = {key: {} for key in seq}

    def get_solution_flows(
        self, questions: dict[str, str | int], modeler_response: ModelerToCoder
    ):
        """生成求解阶段的流程配置。

        Args:
            questions: 包含各问题描述的字典。
            modeler_response: 建模手的响应，包含各问题的解决方案。

        Returns:
            求解流程配置字典，键为任务名，值包含 coder_prompt 等信息。
        """
        questions_quesx = {
            key: value
            for key, value in questions.items()
            if key.startswith("ques") and key != "ques_count"
        }
        solutions = modeler_response.questions_solution
        prompt_prefix = ""
        if self.env_capability:
            prompt_prefix += f"\n{self.env_capability}\n"
        prompt_prefix += f"\n{DATA_DISCIPLINE}\n"
        ques_flow = {
            key: {
                "coder_prompt": f"""
                        {prompt_prefix}参考建模手给出的解决方案{solutions.get(key, "")}
                        完成如下问题{value}
                    """,
            }
            for key, value in questions_quesx.items()
        }
        flows = {
            "eda": {
                "coder_prompt": f"""
                        {prompt_prefix}参考建模手给出的解决方案{solutions.get("eda", "对数据进行探索性分析")}
                        对当前目录下数据进行EDA分析(数据清洗,可视化),清洗后的数据保存当前目录下,**不需要复杂的模型**
                    """,
            },
            **ques_flow,
            "sensitivity_analysis": {
                "coder_prompt": f"""
                        {prompt_prefix}参考建模手给出的解决方案{solutions.get("sensitivity_analysis", "对模型进行灵敏度分析")}
                        完成敏感性分析
                    """,
            },
        }
        return flows

    def get_write_flows(
        self, user_output: UserOutput, config_template: dict, bg_ques_all: str
    ):
        """生成写作阶段的流程配置。

        Args:
            user_output: 用户输出对象，包含已求解的结果。
            config_template: 论文模板配置。
            bg_ques_all: 问题背景和题目信息。

        Returns:
            写作流程配置字典，键为章节名，值为写作提示。
        """
        model_build_solve = user_output.get_model_build_solve()
        # v4 2-2 参数表纪律：任务级 prompt 落点（三期经验——系统提示词会被任务指令压过）
        symbol_req = (
            "，建模方案中显式赋值的关键参数（符号=数值形式）必须全部进入符号说明表，"
            "列全符号/含义/取值/单位/来源"
        )
        flows = {
            "firstPage": f"""问题背景{bg_ques_all},不需要编写代码,根据模型的求解的信息{model_build_solve}，按照如下模板撰写：{config_template["firstPage"]}，撰写标题、中文摘要（五组件：背景/目的/方法/发现/结论，发现带具体数字）、**英文 Abstract（按同五组件独立撰写，150-300 词，禁止逐句对译，关键数字必须与中文摘要完全一致）**、中英文关键词""",
            "RepeatQues": f"""问题背景{bg_ques_all},不需要编写代码,根据模型的求解的信息{model_build_solve}，按照如下模板撰写：{config_template["RepeatQues"]}，撰写问题重述""",
            "analysisQues": f"""问题背景{bg_ques_all},不需要编写代码,根据模型的求解的信息{model_build_solve}，按照如下模板撰写：{config_template["analysisQues"]}，撰写问题分析""",
            "modelAssumption": f"""问题背景{bg_ques_all},不需要编写代码,根据模型的求解的信息{model_build_solve}，按照如下模板撰写：{config_template["modelAssumption"]}，撰写模型假设""",
            "symbol": f"""不需要编写代码,根据模型的求解的信息{model_build_solve}，按照如下模板撰写：{config_template["symbol"]}，撰写符号说明部分{symbol_req}""",
            "judge": f"""不需要编写代码,根据模型的求解的信息{model_build_solve}，按照如下模板撰写：{config_template["judge"]}，撰写模型的评价部分""",
        }
        return flows

    def get_writer_prompt(
        self,
        key: str,
        coder_response: str,
        code_interpreter: BaseCodeInterpreter,
        config_template: dict,
    ) -> str:
        """根据不同的key生成对应的writer_prompt

        Args:
            key: 任务类型
            coder_response: 代码执行结果

        Returns:
            str: 生成的writer_prompt
        """
        code_output = code_interpreter.get_code_output(key)

        questions_quesx_keys = self.get_questions_quesx_keys()
        bgc = self.questions["background"]
        # v4 2-2 参数表纪律：θ/K 等关键参数不落正文则不可复算（08832b52 实证 must_fix）
        param_req = (
            "。**关键参数表**：本问模型使用的全部关键参数必须以表格形式写入正文"
            "（列：符号/含义/取值/单位/来源），建模方案中显式赋值的参数一个都不能缺席；"
            "未给出取值或来源的参数不得作为结论依据"
        )
        quesx_writer_prompt = {
            key: f"""
                    问题背景{bgc},不需要编写代码,代码手得到的结果{coder_response},{code_output},按照如下模板撰写：{config_template[key]}{param_req}
                """
            for key in questions_quesx_keys
        }

        writer_prompt = {
            "eda": f"""
                    问题背景{bgc},不需要编写代码,代码手得到的结果{coder_response},{code_output},按照如下模板撰写：{config_template["eda"]}
                """,
            **quesx_writer_prompt,
            "sensitivity_analysis": f"""
                    问题背景{bgc},不需要编写代码,代码手得到的结果{coder_response},{code_output},按照如下模板撰写：{config_template["sensitivity_analysis"]}{param_req}
                """,
        }

        if key in writer_prompt:
            return writer_prompt[key]
        else:
            raise ValueError(f"未知的任务类型: {key}")

    def get_questions_quesx_keys(self) -> list[str]:
        """获取问题1,2...的键"""
        return list(self.get_questions_quesx().keys())

    def get_questions_quesx(self) -> dict[str, str | int]:
        """获取问题1,2,3...的键值对"""
        # 获取所有以 "ques" 开头的键值对
        questions_quesx = {
            key: value
            for key, value in self.questions.items()
            if key.startswith("ques") and key != "ques_count"
        }
        return questions_quesx

    def get_seq(self, ques_count: int) -> dict[str, str]:
        """获取论文章节顺序。

        Args:
            ques_count: 问题数量。

        Returns:
            以章节名为键的有序字典。
        """
        ques_str = [f"ques{i}" for i in range(1, ques_count + 1)]
        seq = [
            "firstPage",
            "RepeatQues",
            "analysisQues",
            "modelAssumption",
            "symbol",
            "eda",
            *ques_str,
            "sensitivity_analysis",
            "judge",
        ]
        return {key: "" for key in seq}
