"""
配置文件
存放数据库连接信息、DeepSeek API 配置。
"""

import os

# =============================================================================
# 数据库连接配置
# =============================================================================
DB_HOST = "localhost"
DB_PORT = 3306
DB_USER = "readonly"
DB_PASSWORD = "123456"
DB_NAME = "business_db"

# =============================================================================
# DeepSeek API 配置
# =============================================================================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")  # 从环境变量读取
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
