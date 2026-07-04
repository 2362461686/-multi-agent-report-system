"""
配置文件
所有敏感信息均通过环境变量读取，支持 .env 文件本地开发。
生产环境请在系统环境变量或容器 secrets 中配置。

本地开发方式：
  1. 复制 .env.example 为 .env
  2. 填入实际值
  3. 应用启动时自动加载
"""

import os
from pathlib import Path

# 尝试加载 .env 文件（本地开发用，生产环境跳过）
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv 未安装，跳过（不影响生产环境）

# =============================================================================
# 数据库连接配置（均从环境变量读取）
# =============================================================================
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "readonly")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "business_db")

# =============================================================================
# DeepSeek API 配置
# =============================================================================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
