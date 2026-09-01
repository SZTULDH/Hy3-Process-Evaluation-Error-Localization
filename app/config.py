"""全局配置与路径常量。

LLM 后端通过环境变量选择：
  * HY3_API_KEY 存在 -> 使用真实 Hy3（OpenAI 兼容）后端
  * 否则          -> 使用离线 Mock 后端，保证整条评估链路可无 Key 运行
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------- 路径

REPO_ROOT = Path(__file__).resolve().parents[1]

DATASET_ROOT = REPO_ROOT / "datasets" / "code"
RESULTS_DIR = REPO_ROOT / "results"
DOCS_DIR = REPO_ROOT / "docs"

RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- LLM

HY3_API_KEY = os.getenv("HY3_API_KEY") or os.getenv("OPENAI_API_KEY")
HY3_BASE_URL = os.getenv("HY3_BASE_URL", "https://api.hunyuan.cloud.tencent.com/v1")
HY3_MODEL = os.getenv("HY3_MODEL", "hy3")

LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))

# ---------------------------------------------------------------- 沙盒

SANDBOX_TIMEOUT = float(os.getenv("SANDBOX_TIMEOUT", "5"))
# 单个测试用例允许的最大 stdout 字符数，超出即截断，防止死循环刷爆内存
SANDBOX_MAX_OUTPUT_CHARS = int(os.getenv("SANDBOX_MAX_OUTPUT_CHARS", "20000"))
# 递归深度上限，防止爆栈
SANDBOX_RECURSION_LIMIT = int(os.getenv("SANDBOX_RECURSION_LIMIT", "3000"))

# ---------------------------------------------------------------- 评估

# Critic 判定为缺陷时，置信度低于该阈值的段落不计入“错误起始步骤”
CRITIC_CONFIDENCE_THRESHOLD = float(os.getenv("CRITIC_CONFIDENCE_THRESHOLD", "0.5"))

# Solver 强制输出的五个段落标题（顺序即步骤顺序）
SECTION_TITLES = [
    "解题思路",
    "复杂度分析",
    "关键边界与处理策略",
    "代码实现",
    "自测说明",
]

STEP_LABELS = {
    "解题思路": "step_1_approach",
    "复杂度分析": "step_2_complexity",
    "关键边界与处理策略": "step_3_edge_cases",
    "代码实现": "step_4_implementation",
    "自测说明": "step_5_self_test",
}


def llm_backend_name() -> str:
    """返回当前生效的 LLM 后端名称。"""
    if HY3_API_KEY:
        return f"hy3({HY3_MODEL} @ {HY3_BASE_URL})"
    return "mock(offline)"
