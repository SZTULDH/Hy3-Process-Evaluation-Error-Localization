"""全局配置与路径常量。

LLM 后端：
  * 默认真实 Hy3（需 HY3_API_KEY 或 OPENAI_API_KEY）
  * 仅 CLI 显式 --backend mock 时使用离线 Mock
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DATASET_ROOT = REPO_ROOT / "datasets" / "code"
RESULTS_DIR = REPO_ROOT / "results"
DOCS_DIR = REPO_ROOT / "docs"

RESULTS_DIR.mkdir(parents=True, exist_ok=True)

HY3_API_KEY = os.getenv("HY3_API_KEY") or os.getenv("OPENAI_API_KEY")
HY3_BASE_URL = os.getenv("HY3_BASE_URL", "https://api.hunyuan.cloud.tencent.com/v1")
HY3_MODEL = os.getenv("HY3_MODEL", "hy3")
# 慢思考深度：no_think | low | high（工具/Agent 场景推荐 high）
HY3_REASONING_EFFORT = os.getenv("HY3_REASONING_EFFORT", "high")

LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))

SANDBOX_TIMEOUT = float(os.getenv("SANDBOX_TIMEOUT", "5"))
SANDBOX_MAX_OUTPUT_CHARS = int(os.getenv("SANDBOX_MAX_OUTPUT_CHARS", "20000"))
SANDBOX_RECURSION_LIMIT = int(os.getenv("SANDBOX_RECURSION_LIMIT", "3000"))

CRITIC_CONFIDENCE_THRESHOLD = float(os.getenv("CRITIC_CONFIDENCE_THRESHOLD", "0.5"))

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
    if HY3_API_KEY:
        return f"hy3({HY3_MODEL} @ {HY3_BASE_URL})"
    return "mock(offline)"
