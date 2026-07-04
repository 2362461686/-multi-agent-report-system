# 多 Agent 协作的企业报表智能体

基于 DeepSeek 大语言模型 + Streamlit 构建的智能数据分析系统。用户输入中文业务问题，4 个 AI Agent 协作完成 **问题拆解 → SQL 生成 → 安全执行 → 分析报告** 全流程。

## 架构设计

```
用户输入："上个月华南区销售额为什么下降了？"
        ↓
┌─────────────────────────────────────┐
│  Agent 1: 需求分析师 (Planner)       │
│  拆解模糊业务问题为可执行子任务         │
│  → ["查询华南区各品类销售额环比", ...] │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│  Agent 2: SQL工程师 (Coder)          │
│  根据 Schema 和子任务生成 SQL          │
│  → SELECT ... FROM orders WHERE ...  │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│  Agent 3: 安全检查员 (Guard)          │
│  SQL 安全检查 → 执行 → 错误反馈修正    │
│  双重防线：关键词硬检查 + LLM 审查      │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│  Agent 4: 报告分析师 (Reporter)       │
│  综合分析所有结果，生成诊断报告         │
│  → "华南区销售额下降12%，主因是..."    │
└─────────────────────────────────────┘
```

## 项目结构

```
多 Agent 协作的企业报表智能体/
├── .env.example         # 环境变量模板
├── .gitignore
├── requirements.txt     # Python 依赖
├── config.py            # 配置（数据库 + API，均从环境变量读取）
├── agents.py            # 4 个 Agent 类定义
├── prompts.py           # 提示词构建函数（唯一来源）
├── app.py               # Streamlit 主程序
├── README.md
└── 项目架构.txt          # 架构图
```

## 快速开始

### 1. 环境准备

```bash
# 克隆项目
git clone https://github.com/2362461686/-multi-agent-report-system.git
cd -multi-agent-report-system

# 创建虚拟环境并安装依赖
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，填入实际值
```

`.env` 文件内容：

```env
# 数据库连接
DB_HOST=localhost
DB_PORT=3306
DB_USER=readonly
DB_PASSWORD=your_password
DB_NAME=business_db

# DeepSeek API
DEEPSEEK_API_KEY=your_deepseek_api_key
```

### 3. 启动应用

```bash
streamlit run app.py
```

浏览器打开 `http://localhost:8501`。

### 4. 输入问题开始分析

在输入框中输入中文业务问题，例如：

- "上个月哪个产品线利润最高？"
- "今年客户流失的主要原因是哪些？"
- "华南区Q3销售额为什么下降了？"
- "哪些客户的复购率最高？"

## 工作流程

每次提交分析请求后，系统按以下步骤执行：

1. **Step 1 — 问题拆解**：PlannerAgent 将模糊问题拆解为 2-4 个具体的 SQL 子任务
2. **Step 2 — SQL 生成**：CoderAgent 逐个为子任务生成 MySQL SELECT 语句
3. **Step 3 — 安全与执行**：GuardAgent 检查每条 SQL 安全性，通过后在只读账户下执行。SQL 执行失败时，错误信息反馈给 CoderAgent 自动修正重试（最多 2 次）
4. **Step 4 — 分析报告**：ReporterAgent 综合所有查询结果，生成结构化诊断报告（概述 → 关键发现 → 深度分析 → 建议）

### 重试机制

```
SQL 生成 → Guard 安全检查
              ├── 通过 → 执行 SQL
              │           ├── 成功 → ✓
              │           └── 失败 → 错误反馈给 Coder → 修正 SQL → 重试
              └── 不通过 → 语法问题？→ 反馈给 Coder → 重试
                          → 危险关键词？→ 直接跳过
```

## 安全机制

- **只读账户**：数据库使用 `readonly` 用户，应用程序层面确保不会执行写操作
- **双重安全防线**：
  - 第一道：硬检查（关键词匹配 DROP/DELETE/UPDATE 等，不依赖 LLM）
  - 第二道：LLM 审查（语法检查、逻辑一致性验证）
- **仅允许 SELECT**：所有 INSERT/UPDATE/DELETE/DROP 等操作均被拦截

## 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 前端界面 | Streamlit | 纯 Python，零前端代码 |
| 数据库驱动 | pymysql | 纯 Python，无编译依赖 |
| LLM 调用 | OpenAI SDK | DeepSeek 兼容接口 |
| 数据处理 | pandas | DataFrame 表格展示 |
| 环境管理 | python-dotenv | .env 文件本地开发 |

## 模块说明

### `config.py`
全部配置通过环境变量读取，支持 `.env` 文件。数据库连接信息和 API Key 均不硬编码。

### `agents.py`
- `BaseAgent` — 基类，封装 DeepSeek API 调用
- `PlannerAgent` — 需求分析师（3 个 Few-shot 示例）
- `CoderAgent` — SQL 工程师（单表/多表联查/时间对比示例）
- `GuardAgent` — 安全检查员（双重防线）
- `ReporterAgent` — 报告分析师（结构化输出）

### `prompts.py`
三个提示词构建函数，是系统提示词的**唯一来源**：
- `build_planner_prompt(question)` — Planner 用
- `build_coder_prompt(schema, sub_task)` — Coder 用
- `build_reporter_prompt(original_question, all_results)` — Reporter 用

### `app.py`
Streamlit 主程序，包含：
- 侧边栏：数据库 Schema 展示 + 刷新 + 历史记录
- 主区域：4 步执行进度实时展示
- 容错：全链路错误处理，不崩溃

## 依赖版本

```
streamlit>=1.28.0
pymysql>=1.1.0
openai>=1.6.0
pandas>=2.0.0
python-dotenv>=1.0.0
```
