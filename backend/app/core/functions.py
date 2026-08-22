"""工具函数定义模块，为各 Agent 提供可用的工具 schema。"""

# ---- OpenAI 格式（Chat Completions + Responses 共用） ----

coder_tools = [
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "This function allows you to execute Python code and retrieve the terminal output. If the code "
            "generates image output, the function will return the text '[image]'. The code is sent to a "
            "Jupyter kernel for execution. The kernel will remain active after execution, retaining all "
            "variables in memory."
            "You cannot show rich outputs like plots or images, but you can store them in the working directory and point the user to them. ",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The code text"}
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    },
]

writer_tools = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "Search for academic papers via OpenAlex. Returns structured bibliographic metadata (authors, year, venue, DOI, citations) - preferred for formal reference lists.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The query string"}
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Semantic web search via Exa. Stronger for Chinese queries, recent technical resources, and real-world implementations. Use when search_papers returns weak results.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The query string"}
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]

# ---- Anthropic 格式 ----

coder_tools_anthropic = [
    {
        "name": "execute_code",
        "description": "This function allows you to execute Python code and retrieve the terminal output. If the code "
        "generates image output, the function will return the text '[image]'. The code is sent to a "
        "Jupyter kernel for execution. The kernel will remain active after execution, retaining all "
        "variables in memory."
        "You cannot show rich outputs like plots or images, but you can store them in the working directory and point the user to them. ",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code text"}
            },
            "required": ["code"],
        },
    },
]

writer_tools_anthropic = [
    {
        "name": "search_papers",
        "description": "Search for academic papers via OpenAlex. Returns structured bibliographic metadata (authors, year, venue, DOI, citations) - preferred for formal reference lists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The query string"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_web",
        "description": "Semantic web search via Exa. Stronger for Chinese queries, recent technical resources, and real-world implementations. Use when search_papers returns weak results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The query string"}
            },
            "required": ["query"],
        },
    },
]
