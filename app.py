"""
企业经营分析智能助手 —— Streamlit 主程序
多 Agent 协作流程：
  Step 1: PlannerAgent  拆解用户问题 → 子任务列表
  Step 2: CoderAgent    逐个生成 SQL（失败可重试最多2次）
  Step 3: GuardAgent    安全检查 + 数据库执行（失败反馈给 Coder 重试）
  Step 4: ReporterAgent 综合分析 → 自然语言诊断报告

启动方式：
    streamlit run app.py
"""

import re
import pymysql
import pandas as pd
import streamlit as st

import config
from agents import PlannerAgent, CoderAgent, GuardAgent, ReporterAgent
from prompts import build_planner_prompt, build_coder_prompt, build_reporter_prompt


# =============================================================================
# 页面配置
# =============================================================================
st.set_page_config(
    page_title="企业经营分析智能助手",
    page_icon="📊",
    layout="wide",
)


# =============================================================================
# 工具函数
# =============================================================================
def extract_sql(text: str) -> str:
    """
    从 LLM 响应文本中提取纯 SQL 语句。

    LLM 可能返回：纯 SQL 文本、```sql...``` 代码块、```...``` 代码块。
    提取策略：优先匹配 ```sql，其次 ```，最后原样返回。

    Args:
        text: LLM 返回的原始文本

    Returns:
        提取出的纯 SQL 语句
    """
    if not text:
        return ""
    # 匹配 ```sql ... ``` 格式
    match = re.search(r"```sql\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # 匹配 ``` ... ``` 格式
    match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


# =============================================================================
# 数据库操作
# =============================================================================
def get_db_connection():
    """
    创建 pymysql 数据库连接。

    Returns:
        pymysql.Connection 对象

    Raises:
        pymysql.Error: 连接失败时抛出
    """
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def get_schema_info() -> str:
    """
    从 information_schema 获取数据库中所有表的结构信息。

    查询每个表的：表名、字段名、字段类型、是否主键，
    组装为格式化的文本供 LLM 理解数据库结构。

    Returns:
        schema_text: 格式化的表结构文本

    Raises:
        pymysql.Error: 查询失败时抛出
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, COLUMN_KEY
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """,
                (config.DB_NAME,),
            )
            rows = cursor.fetchall()

        if not rows:
            return "（数据库中暂无表）"

        # 按表分组组装文本
        lines = []
        current_table = None
        for row in rows:
            table_name = row["TABLE_NAME"]
            col_name = row["COLUMN_NAME"]
            col_type = row["COLUMN_TYPE"]
            is_pk = "主键" if row["COLUMN_KEY"] == "PRI" else ""

            # 遇到新表时输出表名标题
            if table_name != current_table:
                current_table = table_name
                lines.append(f"表名: {table_name}")
                lines.append("字段:")

            # 输出字段信息
            suffix = f", {is_pk}" if is_pk else ""
            lines.append(f"  - {col_name} ({col_type}{suffix})")

        return "\n".join(lines)
    finally:
        conn.close()


def execute_sql(sql: str) -> tuple[list, list]:
    """
    执行 SQL 查询并返回列名和数据行。

    执行流程：
    1. 创建只读数据库连接
    2. 执行 SELECT 查询
    3. 提取列名和数据行（统一转为 tuple 列表供 DataFrame 使用）
    4. 关闭连接

    Args:
        sql: 待执行的 SQL 语句（已通过安全检查）

    Returns:
        (columns, rows) — columns 是列名字符串列表，rows 是数据 tuple 列表

    Raises:
        pymysql.Error: SQL 执行出错时抛出
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()

            # 从游标的 description 中提取列名
            columns = [desc[0] for desc in cursor.description] if cursor.description else []

            # DictCursor 结果转为 tuple 列表，保证 DataFrame 兼容性
            row_tuples = [tuple(r[col] for col in columns) for r in rows]
            return columns, row_tuples
    finally:
        conn.close()


# =============================================================================
# 初始化 session_state
# =============================================================================
def init_session():
    """初始化所有 session_state 变量（跨页面请求保持状态）。"""
    defaults = {
        "schema_text": "",          # 数据库 Schema 文本
        "schema_loaded": False,     # Schema 是否已成功加载
        "schema_error": "",         # Schema 加载错误信息
        "chat_history": [],         # 历史对话记录
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_session()


# =============================================================================
# 侧边栏：数据库 Schema 展示 + 控制按钮
# =============================================================================
with st.sidebar:
    st.header("📋 数据库结构")

    # 刷新 Schema 按钮：点击后标记为未加载，下次渲染时重新获取
    if st.button("🔄 刷新 Schema", use_container_width=True):
        st.session_state.schema_loaded = False
        st.session_state.schema_text = ""
        st.session_state.schema_error = ""

    # 只在未加载状态下尝试获取 Schema（避免每次重渲染都连接数据库）
    if not st.session_state.schema_loaded:
        with st.spinner("正在加载数据库 Schema ..."):
            try:
                st.session_state.schema_text = get_schema_info()
                st.session_state.schema_loaded = True
                st.session_state.schema_error = ""
            except Exception as e:
                st.session_state.schema_error = str(e)
                st.session_state.schema_loaded = True  # 标记已尝试

    # 显示 Schema 或连接错误
    if st.session_state.schema_error:
        st.error(f"❌ 数据库连接失败\n\n```\n{st.session_state.schema_error}\n```")
        st.info("请检查 `config.py` 中的数据库配置，确保 MySQL 服务已启动。")
    else:
        with st.expander("查看所有表结构", expanded=True):
            if st.session_state.schema_text:
                st.code(st.session_state.schema_text, language=None)
            else:
                st.warning("数据库中没有找到任何表。")

    st.divider()

    # 清空历史按钮
    if st.button("🗑️ 清除对话历史", use_container_width=True):
        st.session_state.chat_history = []
        st.rerun()

    # 历史记录折叠展示
    if st.session_state.chat_history:
        with st.expander(f"📜 历史记录（{len(st.session_state.chat_history)} 条）", expanded=False):
            for i, rec in enumerate(st.session_state.chat_history, 1):
                st.markdown(f"**{i}.** {rec['question']}")
                st.caption(f"任务数: {rec.get('task_count', 'N/A')} | "
                           f"成功: {rec.get('success_count', 'N/A')}")
                st.divider()


# =============================================================================
# 主区域
# =============================================================================
st.title("📊 企业经营分析智能助手")
st.caption("输入中文业务问题，4 个 AI Agent 协作完成数据查询与智能分析")

col_input, col_btn = st.columns([5, 1])

with col_input:
    user_question = st.text_input(
        label="",
        placeholder="例如：上个月哪个产品线利润最高？",
        key="question_input",
        label_visibility="collapsed",
    )

with col_btn:
    submit_clicked = st.button("🔎 开始分析", type="primary", use_container_width=True)


# =============================================================================
# 核心流程：多 Agent 协作执行
# =============================================================================
if submit_clicked and user_question.strip():
    question = user_question.strip()

    # ===================== 前置检查 =====================
    if not st.session_state.schema_text:
        st.error("❌ 数据库 Schema 未加载，无法执行分析。请确认数据库连接正常。")
        st.stop()

    if not config.DEEPSEEK_API_KEY:
        st.error(
            "❌ DeepSeek API Key 未配置！\n\n"
            "请设置环境变量 `DEEPSEEK_API_KEY`：\n"
            "```bash\nexport DEEPSEEK_API_KEY=your_key_here\n```"
        )
        st.stop()

    # 展示用户问题
    st.markdown("---")
    st.markdown(f"### 📝 分析问题")
    st.info(f"**{question}**")

    # ===================== 初始化 Agent =====================
    planner = PlannerAgent()
    coder = CoderAgent()
    guard = GuardAgent()
    reporter = ReporterAgent()

    schema_text = st.session_state.schema_text

    # ===================== Step 1：Planner 拆解问题 =====================
    with st.spinner("🤔 **Step 1/4** — 需求分析师正在拆解问题 ..."):
        try:
            # 使用 prompts.py 统一构建提示词，传入 agent 的 run() 方法
            planner_messages = build_planner_prompt(question)
            sub_tasks = planner.run(question, messages=planner_messages)

            if not isinstance(sub_tasks, list) or len(sub_tasks) == 0:
                st.error("❌ 需求分析师未能正确拆解问题，请尝试更具体的描述。")
                st.stop()

        except Exception as e:
            st.error(f"❌ 需求分析师调用失败\n\n```\n{str(e)}\n```")
            st.stop()

    # 展示拆解结果
    with st.expander("🔍 **步骤1：问题拆解 — 需求分析师**", expanded=True):
        st.caption(f"共拆解为 **{len(sub_tasks)}** 个子任务：")
        for i, task in enumerate(sub_tasks, 1):
            st.markdown(f"**{i}.** {task}")

    # ===================== Step 2 & 3：SQL生成 + 安全 + 执行 =====================
    st.markdown("---")
    st.markdown("### ⚙️ 步骤2 & 3：SQL 生成、安全检查与执行")

    # 存储所有子任务的最终结果
    all_results = []
    MAX_RETRIES = 2  # 最多重试次数

    for task_idx, task in enumerate(sub_tasks, 1):
        # ---- 当前子任务的容器（所有重试都在此处展示）----
        task_container = st.container()

        task_result = {
            "task": task,
            "sql": "",
            "safe": False,
            "reason": "",
            "columns": [],
            "rows": [],
            "error": "",
            "status": "pending",
        }

        # ---- 重试循环 ----
        for attempt in range(1, MAX_RETRIES + 2):  # 1次初始 + MAX_RETRIES次重试
            # 显示当前尝试进度
            if attempt == 1:
                progress_text = f"⏳ **子任务 {task_idx}/{len(sub_tasks)}** — {task}"
            else:
                progress_text = (f"🔄 **子任务 {task_idx}/{len(sub_tasks)}** "
                                 f"— 重试第 {attempt - 1}/{MAX_RETRIES} 次")

            # 在容器内显示进度
            with task_container:
                st.caption(progress_text)

            # ---- 2a: CoderAgent 生成 SQL ----
            sql = ""
            try:
                # 使用 prompts.py 统一构建提示词
                coder_messages = build_coder_prompt(schema_text, task)

                # 如果是重试，附加错误修正提示
                if attempt > 1 and task_result.get("error"):
                    coder_messages[1]["content"] += (
                        f"\n\n【修正提示】上一次 SQL 执行失败，请修正：\n"
                        f"错误信息：{task_result['error']}\n"
                        f"上一次 SQL：{task_result['sql']}\n"
                        f"请确保字段名与 Schema 一致，语法正确。"
                    )

                sql = coder.run(task, messages=coder_messages)
                sql = extract_sql(sql)
                task_result["sql"] = sql

            except Exception as e:
                task_result["error"] = f"CoderAgent 调用失败: {str(e)}"
                task_result["status"] = "failed"
                with task_container:
                    st.error(f"❌ SQL 生成失败: {task_result['error']}")
                break  # API 调用失败不重试

            # ---- 2b: 展示生成的 SQL ----
            with task_container:
                sql_label = (
                    f"📝 子任务 {task_idx} SQL"
                    if attempt == 1
                    else f"📝 子任务 {task_idx} SQL（重试{attempt - 1}）"
                )
                with st.expander(sql_label, expanded=(attempt == 1)):
                    st.code(sql, language="sql")

            # ---- 3a: GuardAgent 安全检查 ----
            guard_failed = False
            try:
                guard_result = guard.run(sql)
                task_result["safe"] = guard_result.get("safe", False)
                task_result["reason"] = guard_result.get("reason", "")
            except Exception as e:
                task_result["safe"] = False
                task_result["reason"] = f"安全检查调用异常: {str(e)}"
                guard_failed = True

            with task_container:
                if not task_result["safe"]:
                    st.warning(f"🛡️ 安全检查不通过: {task_result['reason']}")
                else:
                    st.caption(f"🛡️ 安全检查通过 ✅")

            # ---- 3b: 安全检查不通过的处理 ----
            if not task_result["safe"]:
                # 判断是否值得重试（语法问题可重试，危险关键词不可重试）
                skip_keywords = ["DROP", "DELETE", "TRUNCATE", "UPDATE", "INSERT",
                                 "ALTER", "CREATE", "EXEC", "INTO"]
                should_skip = any(
                    kw in task_result.get("reason", "").upper() for kw in skip_keywords
                )

                if should_skip or attempt > MAX_RETRIES:
                    # 危险操作或重试次数耗尽，放弃此子任务
                    task_result["error"] = f"安全检查不通过: {task_result['reason']}"
                    task_result["status"] = "skipped"
                    break
                else:
                    # 语法类问题，反馈给 Coder 重试
                    task_result["error"] = f"安全检查不通过: {task_result['reason']}"
                    continue  # 进入下一次重试

            # ---- 3c: 执行 SQL ----
            try:
                columns, rows = execute_sql(sql)
                task_result["columns"] = columns
                task_result["rows"] = rows
                task_result["status"] = "success"
                task_result["error"] = ""

                with task_container:
                    st.caption(f"✅ 查询执行成功 — 返回 **{len(rows)}** 条记录")
                break  # 成功，退出重试循环

            except Exception as e:
                error_msg = str(e)
                task_result["error"] = error_msg

                with task_container:
                    st.warning(f"⚠️ SQL 执行出错: {error_msg[:150]}")

                if attempt > MAX_RETRIES:
                    # 重试次数耗尽
                    task_result["status"] = "failed"
                    break
                else:
                    # 还有重试机会
                    continue  # Coder 会收到错误反馈重新生成

        # ---- 展示本子任务的最终结果 ----
        with task_container:
            if task_result["status"] == "success":
                with st.expander(
                    f"✅ 子任务 {task_idx} 结果 — {task}（{len(task_result['rows'])} 条）",
                    expanded=(task_idx <= 2),
                ):
                    if task_result["rows"]:
                        df = pd.DataFrame(task_result["rows"], columns=task_result["columns"])
                        st.dataframe(df, use_container_width=True)
                    else:
                        st.info("查询无返回数据。")

            elif task_result["status"] == "failed":
                with st.expander(
                    f"❌ 子任务 {task_idx} 失败 — {task}（已重试 {MAX_RETRIES} 次）",
                    expanded=False,
                ):
                    st.error(f"**错误**: {task_result.get('error', '未知')}")
                    if task_result.get("sql"):
                        st.code(task_result["sql"], language="sql")

            elif task_result["status"] == "skipped":
                with st.expander(
                    f"⚠️ 子任务 {task_idx} 已跳过 — {task}",
                    expanded=False,
                ):
                    st.warning(f"**原因**: {task_result.get('error', '未知')}")
                    if task_result.get("sql"):
                        st.code(task_result["sql"], language="sql")

        # 收集结果
        all_results.append(task_result)

    # ---- 统计汇总 ----
    success_count = sum(1 for r in all_results if r["status"] == "success")
    failed_count = sum(1 for r in all_results if r["status"] in ("failed", "skipped"))

    st.markdown("---")
    if failed_count > 0:
        st.warning(
            f"📊 查询统计：**{success_count}** 个成功，**{failed_count}** 个失败/跳过 "
            f"（共 {len(all_results)} 个子任务）"
        )
    else:
        st.success(f"📊 全部 **{success_count}** 个子任务查询完成")

    # ===================== Step 4：Reporter 综合分析 =====================
    st.markdown("---")
    st.markdown("### 📄 步骤4：综合分析报告")

    with st.spinner("🧠 **Step 4/4** — 报告分析师正在综合分析所有数据 ..."):
        # 将查询结果组装为 Reporter 的输入文本
        results_parts = []
        for i, r in enumerate(all_results, 1):
            part = f"子任务{i}: {r['task']}\n"

            if r["status"] == "success":
                part += f"SQL: {r['sql']}\n"
                part += f"结果列: {', '.join(r['columns']) if r['columns'] else '无'}\n"
                if r["rows"]:
                    # 截取前 20 行防止 LLM 上下文溢出
                    preview = r["rows"][:20]
                    part += f"数据行数: {len(r['rows'])}（以下为前{len(preview)}行）\n"
                    for row in preview:
                        part += f"  {row}\n"
                else:
                    part += "数据: 无返回数据\n"
            else:
                part += f"状态: {r['status']}\n"
                part += f"错误: {r.get('error', '未知')}\n"

            results_parts.append(part)

        results_summary = "\n".join(results_parts)

        try:
            # 使用 prompts.py 构建提示词，通过 reporter.run() 统一调用
            reporter_messages = build_reporter_prompt(question, results_summary)
            final_report = reporter.run(results_summary, messages=reporter_messages)

            # 用 markdown 渲染最终报告（重点数据在 prompt 中已要求加粗）
            st.markdown(final_report)

            # 保存到历史记录
            st.session_state.chat_history.append({
                "question": question,
                "task_count": len(all_results),
                "success_count": success_count,
            })

        except Exception as e:
            st.error(f"❌ 报告分析师调用失败\n\n```\n{str(e)}\n```")


# =============================================================================
# 底部信息
# =============================================================================
st.divider()
st.caption(
    "🤖 由 4 个 AI Agent 协作完成 | "
    "Planner → Coder → Guard → Reporter | "
    "仅支持 SELECT 查询，所有危险操作已被拦截"
)
