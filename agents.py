"""
多 Agent 协作模块
包含4个专门的 Agent 类，通过 DeepSeek API（OpenAI 兼容模式）完成各自职责：
  - PlannerAgent: 需求分析师 —— 拆解模糊业务问题为可执行子任务
  - CoderAgent:   SQL 工程师   —— 根据 Schema 和子任务生成 SQL
  - GuardAgent:   安全检查员   —— 审查 SQL 安全性
  - ReporterAgent: 报告分析师   —— 综合分析查询结果，生成诊断报告
"""

import json
import re
from openai import OpenAI
import config


# =============================================================================
# BaseAgent —— Agent 基类，封装 DeepSeek API 调用逻辑
# =============================================================================
class BaseAgent:
    """
    Agent 基类，提供通用的 DeepSeek API 调用能力。
    所有具体 Agent 均继承此类，只需实现各自的 run() 方法。
    """

    def __init__(self, name: str, system_prompt: str):
        """
        初始化 Agent。

        Args:
            name: Agent 名称（用于日志和标识）
            system_prompt: 系统提示词，定义 Agent 的角色和行为
        """
        self.name = name
        self.system_prompt = system_prompt
        # 初始化 OpenAI 客户端，指向 DeepSeek 兼容接口
        self.client = OpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
        )

    def _call_api(self, messages: list[dict], temperature: float = 0.1) -> str:
        """
        调用 DeepSeek API，返回模型响应文本。

        Args:
            messages: OpenAI 兼容格式的消息列表
            temperature: 生成温度（0.0 = 确定性最高，1.0 = 随机性最高）

        Returns:
            模型返回的文本内容
        """
        response = self.client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=4096,
        )
        return response.choices[0].message.content

    def run(self, user_input: str):
        """
        执行 Agent 的核心任务（由子类实现）。

        Args:
            user_input: 用户的输入内容

        Raises:
            NotImplementedError: 子类必须重写此方法
        """
        raise NotImplementedError("子类必须实现 run() 方法")


# =============================================================================
# Agent 1: PlannerAgent —— 需求分析师
# 职责：理解模糊的业务问题，拆解成多个可执行的 SQL 子任务
# =============================================================================
class PlannerAgent(BaseAgent):
    """
    需求分析师 Agent。
    将用户的模糊业务问题拆解为结构化的子任务列表，
    每个子任务都是可直接转化为 SQL 查询的明确需求。
    """

    def __init__(self):
        system_prompt = (
            "你是一个资深业务分析师。"
            "你的任务是把用户的模糊业务问题拆解成多个具体的、可以用 SQL 查询的子任务。\n\n"
            "规则：\n"
            "1. 每个子任务必须是明确、具体、可量化的查询需求\n"
            "2. 子任务之间应相互独立，避免重叠\n"
            "3. 考虑多维度分析：时间对比、品类拆分、渠道对比、客户分层等\n"
            "4. 输出格式：必须输出纯 JSON 数组，每个元素是一个子任务描述字符串\n"
            "5. 不要输出任何其他内容，不要 markdown 代码块包裹\n\n"
            "---- 示例1 ----\n"
            "输入：上个月华南区销售额为什么下降了？\n"
            '输出：["查询上个月华南区各产品品类的销售额及环比变化", '
            '"查询上个月华南区主要客户的订单量变化", '
            '"查询上个月华南区退货率及与上上月对比", '
            '"查询上个月华南区各销售渠道的业绩分布"]\n\n"
            "---- 示例2 ----\n"
            "输入：公司今年表现最好的产品是哪些？\n"
            '输出：["查询今年各产品的总销售额并按降序排列", '
            '"查询今年各产品的销售增长率", '
            '"查询今年各产品的客户复购率", '
            '"查询今年各产品的利润贡献排名"]\n\n"
            "---- 示例3 ----\n"
            "输入：哪些客户有流失风险？\n"
            '输出：["查询近3个月未下单的客户列表及最后下单时间", '
            '"查询各客户的订单频率变化趋势", '
            '"查询近期有投诉或退货记录的客户", '
            '"查询各客户最近一笔订单金额与前3个月平均订单金额的对比"]'
        )
        super().__init__("需求分析师", system_prompt)

    def run(self, user_input: str) -> list[str]:
        """
        将用户的业务问题拆解为子任务列表。

        Args:
            user_input: 用户的中文自然语言问题（如"上个月华南区销售额为什么下降了？"）

        Returns:
            子任务描述字符串列表，如 ["查询A", "查询B", ...]
            解析失败时返回原始所有非空行作为后备
        """
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_input},
        ]
        response_text = self._call_api(messages).strip()

        # ---- 尝试解析 JSON 数组 ----
        try:
            # 移除可能的 markdown 代码块包裹
            match = re.search(
                r"```(?:json)?\s*(\[.*?\])\s*```", response_text, re.DOTALL
            )
            if match:
                response_text = match.group(1)
            tasks = json.loads(response_text)
            if isinstance(tasks, list):
                return tasks
        except json.JSONDecodeError:
            pass

        # ---- 后备方案：按行拆分，去除序号前缀 ----
        lines = [
            re.sub(r"^[\d]+[.、)\-\s]+", "", line).strip()
            for line in response_text.split("\n")
            if line.strip()
        ]
        return lines if lines else [response_text]


# =============================================================================
# Agent 2: CoderAgent —— SQL 工程师
# 职责：根据数据库 Schema 和子任务描述，生成正确的 MySQL SELECT 语句
# =============================================================================
class CoderAgent(BaseAgent):
    """
    SQL 工程师 Agent。
    根据数据库表结构和具体的查询需求，生成可执行的 MySQL SELECT 语句。
    内置 Few-shot 示例，帮助模型理解输出格式。
    """

    def __init__(self):
        system_prompt = (
            "你是一个资深 SQL 工程师。"
            "根据提供的数据库 Schema 和查询需求，写出对应的 MySQL SELECT 查询语句。\n\n"
            "规则：\n"
            "1. 只输出纯 SQL 语句，以分号结尾\n"
            "2. 不要输出任何解释、说明或其他文字\n"
            "3. 不要使用 markdown 代码块（不要 ``` 包裹）\n"
            "4. 只生成 SELECT 查询，不生成 INSERT/UPDATE/DELETE/DROP 等修改操作\n"
            "5. 表名和字段名使用反引号包裹（如 `table_name`）以避免关键字冲突\n"
            "6. 字符串值使用单引号\n\n"
            "---- 示例1（单表查询）----\n"
            "Schema:\n"
            "表名: orders\n"
            "字段: id(int,主键), product_name(varchar), amount(decimal), "
            "region(varchar), order_date(date)\n"
            "需求：查询华南区上个月各产品的销售额\n"
            "输出：\n"
            "SELECT product_name, SUM(amount) AS total_sales "
            "FROM orders "
            "WHERE region = '华南区' "
            "AND order_date >= DATE_SUB(CURRENT_DATE, INTERVAL 1 MONTH) "
            "GROUP BY product_name "
            "ORDER BY total_sales DESC;\n\n"
            "---- 示例2（多表联查）----\n"
            "Schema:\n"
            "表名: customers\n"
            "字段: id(int,主键), name(varchar), city(varchar), level(varchar)\n"
            "表名: orders\n"
            "字段: id(int,主键), customer_id(int), total_amount(decimal), "
            "order_date(date), status(varchar)\n"
            "需求：查询各城市的客户总消费金额，按消费总额从高到低排序\n"
            "输出：\n"
            "SELECT c.city, SUM(o.total_amount) AS total_spent "
            "FROM customers c "
            "INNER JOIN orders o ON c.id = o.customer_id "
            "GROUP BY c.city "
            "ORDER BY total_spent DESC;\n\n"
            "---- 示例3（时间对比）----\n"
            "Schema:\n"
            "表名: sales\n"
            "字段: id(int,主键), product_id(int), amount(decimal), "
            "sale_date(date), channel(varchar)\n"
            "需求：查询本月与上月各渠道销售额的环比变化\n"
            "输出：\n"
            "SELECT "
            "channel, "
            "SUM(CASE WHEN sale_date >= DATE_SUB(CURRENT_DATE, INTERVAL 1 MONTH) "
            "THEN amount ELSE 0 END) AS current_month, "
            "SUM(CASE WHEN sale_date >= DATE_SUB(CURRENT_DATE, INTERVAL 2 MONTH) "
            "AND sale_date < DATE_SUB(CURRENT_DATE, INTERVAL 1 MONTH) "
            "THEN amount ELSE 0 END) AS last_month "
            "FROM sales "
            "GROUP BY channel "
            "ORDER BY current_month DESC;"
        )
        super().__init__("SQL工程师", system_prompt)

    def run(self, user_input: str) -> str:
        """
        根据 Schema 和子任务需求生成 SQL 语句。

        Args:
            user_input: 包含数据库 Schema 和查询需求的文本
                       格式建议："Schema:\n{表结构}\n\n需求：{子任务描述}"

        Returns:
            清理后的纯 SQL 语句（已确保以分号结尾）
        """
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_input},
        ]
        # 使用低温度以获得确定性的 SQL 输出
        sql = self._call_api(messages, temperature=0.0).strip()

        # ---- 清理响应：移除 markdown 代码块 ----
        match = re.search(r"```(?:sql)?\s*(.*?)\s*```", sql, re.DOTALL | re.IGNORECASE)
        if match:
            sql = match.group(1).strip()

        # ---- 移除首尾可能的空白和换行 ----
        sql = sql.strip()

        # ---- 确保以分号结尾 ----
        if not sql.endswith(";"):
            sql += ";"

        return sql


# =============================================================================
# Agent 3: GuardAgent —— 安全检查员
# 职责：审查 SQL 安全性，检查是否包含危险操作
# =============================================================================
class GuardAgent(BaseAgent):
    """
    安全检查员 Agent。
    检查 SQL 语句是否包含危险操作（DROP、DELETE 等），以及语法是否有明显错误。
    同时支持关键词硬检查作为后备，确保安全检查不依赖 LLM 的可靠性。
    """

    # 禁止的 SQL 关键词列表（后备检查用）
    FORBIDDEN_KEYWORDS = [
        "DROP", "DELETE", "TRUNCATE", "UPDATE", "INSERT",
        "ALTER", "CREATE", "EXEC", "EXECUTE", "GRANT", "REVOKE",
        "INTO OUTFILE", "INTO DUMPFILE", "LOAD DATA",
    ]

    def __init__(self):
        system_prompt = (
            "你是一个数据库安全审查员。"
            "检查 SQL 语句是否安全以及语法是否有明显错误。\n\n"
            "安全检查规则：\n"
            "1. 包含以下关键词则不安全：DROP, DELETE, TRUNCATE, UPDATE, INSERT, "
            "ALTER, CREATE, EXEC, EXECUTE, GRANT, REVOKE, INTO OUTFILE, INTO DUMPFILE\n"
            "2. 必须以 SELECT 开头才是安全的查询语句\n"
            "3. 检查基本的 SQL 语法错误：括号不匹配、关键字拼写错误、缺少必要的关键字等\n\n"
            "输出格式：必须输出纯 JSON 对象，不要任何其他内容，不要 markdown 包裹\n"
            '格式：{"safe": true/false, "reason": "安全或具体的不安全原因说明"}\n\n'
            "---- 示例1 ----\n"
            "输入：SELECT * FROM users WHERE id = 1;\n"
            '输出：{"safe": true, "reason": "标准的SELECT查询语句，语法正确"}\n\n'
            "---- 示例2 ----\n"
            "输入：DROP TABLE users;\n"
            '输出：{"safe": false, "reason": "包含危险操作 DROP TABLE，不允许删除表"}\n\n'
            "---- 示例3 ----\n"
            "输入：SELECT FROM WHERE;\n"
            '输出：{"safe": false, "reason": "SQL语法错误：缺少字段列表和表名"}'
        )
        super().__init__("安全检查员", system_prompt)

    def _hard_check(self, sql: str) -> dict:
        """
        后备硬检查：用关键词匹配方式检查 SQL 安全性。
        不依赖 LLM 调用，直接对 SQL 文本进行模式匹配。

        Args:
            sql: 待检查的 SQL 语句

        Returns:
            {"safe": bool, "reason": str}
        """
        sql_upper = sql.upper().strip()

        # 检查是否以 SELECT 开头（去除注释和空白后）
        if not sql_upper.startswith("SELECT"):
            return {
                "safe": False,
                "reason": "SQL 语句必须以 SELECT 开头，当前语句以其他关键字开头",
            }

        # 检查是否包含禁止关键词（整词匹配）
        # 将 SQL 按非字母数字字符拆分
        words = set(re.split(r"[^a-zA-Z0-9_]+", sql_upper))
        words.discard("")
        for keyword in self.FORBIDDEN_KEYWORDS:
            # 多词关键词（如 INTO OUTFILE）需要整串检查
            if " " in keyword:
                if keyword in sql_upper:
                    return {
                        "safe": False,
                        "reason": f"包含禁止的关键词: {keyword}",
                    }
            else:
                if keyword in words:
                    return {
                        "safe": False,
                        "reason": f"包含禁止的关键词: {keyword}",
                    }

        # 检查 SQL 注释符号（防止注释绕过）
        if "--" in sql or "/*" in sql:
            return {
                "safe": False,
                "reason": "SQL 中包含注释符号，可能用于绕过安全检查",
            }

        return {"safe": True, "reason": "通过硬性安全检查"}

    def run(self, user_input: str) -> dict:
        """
        检查 SQL 语句的安全性。

        先通过硬检查（关键词匹配）做第一道防线，
        再调用 LLM 做辅助审查（语法检查等）。

        Args:
            user_input: 待检查的 SQL 语句

        Returns:
            {"safe": bool, "reason": str}  安全判断及原因说明
        """
        # ---- 第一道防线：硬检查（不依赖 LLM）----
        hard_result = self._hard_check(user_input)
        if not hard_result["safe"]:
            return hard_result

        # ---- 第二道防线：LLM 辅助审查（语法检查）----
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"请检查以下 SQL 语句的安全性：\n{user_input}"},
        ]
        response_text = self._call_api(messages, temperature=0.0).strip()

        # ---- 尝试解析 LLM 返回的 JSON ----
        try:
            match = re.search(
                r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL
            )
            if match:
                response_text = match.group(1)
            result = json.loads(response_text)
            return result
        except json.JSONDecodeError:
            # LLM 返回无法解析时，以硬检查结果为准
            return hard_result


# =============================================================================
# Agent 4: ReporterAgent —— 报告分析师
# 职责：综合分析所有查询结果，生成自然语言诊断报告
# =============================================================================
class ReporterAgent(BaseAgent):
    """
    报告分析师 Agent。
    接收所有 SQL 查询的结果数据，综合分析后生成结构化的诊断报告。
    用自然语言解释数据含义，指出关键发现并给出可操作建议。
    """

    def __init__(self):
        system_prompt = (
            "你是一个资深数据分析师。"
            "根据多个 SQL 查询的结果，综合分析并用自然语言回答用户的原始问题。\n\n"
            "报告要求：\n"
            "1. **概述**：用 1-2 句话总结整体情况\n"
            "2. **关键发现**：列出 2-4 个最重要的数据发现，每个发现用具体数字支撑\n"
            "3. **深度分析**：分析数据之间的关联、趋势和潜在原因\n"
            "4. **可操作建议**：基于分析给出 2-3 条具体的改进建议\n"
            "5. 如果数据不足以得出结论，明确说明缺少哪些数据\n"
            "6. 语言专业但不晦涩，让非技术人员也能理解\n\n"
            "示例输出格式：\n"
            "【概述】\n华南区上个月销售额环比下降12.3%，主要由电子品类下滑导致。\n\n"
            "【关键发现】\n"
            "1. 电子品类销售额从520万降至380万，降幅26.9%，是最大拖累因素\n"
            "2. 该品类退货率从3.2%飙升至9.8%，集中在某品牌路由器的批量退货\n"
            "3. 华南区活跃客户数减少15%，但客单价上升8%，说明流失的主要是中小客户\n\n"
            "【深度分析】\n...\n\n"
            "【建议】\n1. ...\n2. ..."
        )
        super().__init__("报告分析师", system_prompt)

    def run(self, user_input: str) -> str:
        """
        根据查询结果生成综合分析报告。

        Args:
            user_input: 包含原始问题和所有查询结果的文本
                       格式建议：
                       "原始问题：{用户的问题}\n\n"
                       "查询结果：\n子任务1: ...\n结果: ...\n\n子任务2: ...\n结果: ..."

        Returns:
            自然语言分析报告文本
        """
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_input},
        ]
        # 使用稍高温度使报告语言更自然
        report = self._call_api(messages, temperature=0.3).strip()
        return report
