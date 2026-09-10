"""全局配置与路径常量。

LLM 后端：
  * 默认真实 Hy3（需 HY3_API_KEY 或 OPENAI_API_KEY）
  * 仅 CLI 显式 --backend mock 时使用离线 Mock
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """从仓库根 .env 补全环境变量（标准库实现，不引入依赖）。

    仅填补 os.environ 的空缺，**不覆盖已显式设置的值**；无 .env 时静默跳过。
    .env 已在 .gitignore 中，适合放 API Key，避免出现在命令行历史里。
    """
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


_load_dotenv()

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
# 思考强度：off | low | medium | high | auto（auto = 跟随模型默认）
# 兼容旧写法：disabled 视作 off，enabled 视作开启但不指定推理深度
HY3_THINKING = os.getenv("HY3_THINKING", "off").strip().lower()
# 推理深度兜底：留空表示由「思考强度」推导，不额外下发 reasoning_effort
HY3_REASONING_EFFORT = os.getenv("HY3_REASONING_EFFORT", "").strip().lower()

LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))

# ------------------------------------------------- 模型清单与思考强度

# 依据官方《深度思考》文档整理。
#   thinking_default : 不显式指定时模型的默认思考行为
#   can_disable      : 是否接受 thinking.type=disabled（False = 强制思考，关不掉）
#   efforts          : 该模型明确支持的 reasoning_effort 值；空列表=未限定，按 low/medium/high 透传
#   effort_default   : 模型默认推理深度
MODEL_CATALOG: list[dict] = [
    {"id": "hy4-preview", "label": "Hy4 preview", "thinking_default": "enabled",
     "can_disable": True, "efforts": ["none", "high"], "effort_default": "high",
     "note": "支持 none/high 两档"},
    {"id": "hy3", "label": "Hy3", "thinking_default": "enabled",
     "can_disable": True, "efforts": [], "effort_default": "high", "note": ""},
    {"id": "hy3-preview", "label": "Hy3 preview", "thinking_default": "disabled",
     "can_disable": True, "efforts": [], "effort_default": "low", "note": "默认不思考"},
    {"id": "deepseek-v4-flash", "label": "DeepSeek-V4-Flash", "thinking_default": "enabled",
     "can_disable": True, "efforts": [], "effort_default": "high", "note": ""},
    {"id": "deepseek-v4-flash-202605", "label": "DeepSeek-V4-Flash（原厂直连）",
     "thinking_default": "enabled", "can_disable": True, "efforts": [],
     "effort_default": "high", "note": ""},
    {"id": "deepseek-v4-pro", "label": "DeepSeek-V4-Pro", "thinking_default": "enabled",
     "can_disable": True, "efforts": [], "effort_default": "high", "note": ""},
    {"id": "deepseek-v4-pro-202606", "label": "DeepSeek-V4-Pro（原厂直连）",
     "thinking_default": "enabled", "can_disable": True, "efforts": [],
     "effort_default": "high", "note": ""},
    {"id": "deepseek-v3.2", "label": "DeepSeek-V3.2", "thinking_default": "enabled",
     "can_disable": True, "efforts": [], "effort_default": "high", "note": ""},
    {"id": "glm-5.3", "label": "GLM-5.3", "thinking_default": "enabled",
     "can_disable": False, "efforts": ["low", "high", "max"], "effort_default": "max",
     "note": "不支持关闭思考"},
    {"id": "glm-5.3-flash", "label": "GLM-5.3-Flash", "thinking_default": "enabled",
     "can_disable": False, "efforts": ["low", "high", "max"], "effort_default": "max",
     "note": "不支持关闭思考"},
    {"id": "glm-5.2", "label": "GLM-5.2", "thinking_default": "enabled",
     "can_disable": True,
     "efforts": ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
     "effort_default": "max", "note": "档位最全"},
    {"id": "glm-5.1", "label": "GLM-5.1", "thinking_default": "enabled",
     "can_disable": True, "efforts": [], "effort_default": "", "note": ""},
    {"id": "glm-5", "label": "GLM-5", "thinking_default": "enabled",
     "can_disable": True, "efforts": [], "effort_default": "", "note": ""},
    {"id": "glm-5-turbo", "label": "GLM-5-Turbo", "thinking_default": "enabled",
     "can_disable": True, "efforts": [], "effort_default": "", "note": ""},
    {"id": "glm-5v-turbo", "label": "GLM-5V-Turbo", "thinking_default": "enabled",
     "can_disable": True, "efforts": [], "effort_default": "", "note": ""},
    {"id": "kimi-k2.7-code", "label": "Kimi-K2.7-Code", "thinking_default": "enabled",
     "can_disable": False, "efforts": [], "effort_default": "",
     "note": "不支持关闭思考"},
    {"id": "kimi-k2.7-code-highspeed", "label": "Kimi-K2.7-Code-HighSpeed",
     "thinking_default": "enabled", "can_disable": False, "efforts": [],
     "effort_default": "", "note": "不支持关闭思考"},
    {"id": "kimi-k2.6", "label": "Kimi-K2.6", "thinking_default": "enabled",
     "can_disable": True, "efforts": [], "effort_default": "", "note": ""},
    {"id": "kimi-k2.5", "label": "Kimi-K2.5", "thinking_default": "enabled",
     "can_disable": True, "efforts": [], "effort_default": "", "note": ""},
    {"id": "minimax-m3", "label": "MiniMax-M3", "thinking_default": "adaptive",
     "can_disable": True, "efforts": [], "effort_default": "", "note": "默认自适应"},
    {"id": "minimax-m2.7", "label": "MiniMax-M2.7", "thinking_default": "enabled",
     "can_disable": False, "efforts": [], "effort_default": "",
     "note": "不支持关闭思考"},
    {"id": "minimax-m2.5", "label": "MiniMax-M2.5", "thinking_default": "enabled",
     "can_disable": False, "efforts": [], "effort_default": "",
     "note": "不支持关闭思考"},
    {"id": "qwen3.5-plus", "label": "Qwen3.5-Plus", "thinking_default": "enabled",
     "can_disable": True, "efforts": [], "effort_default": "", "note": ""},
    {"id": "qwen3.5-flash", "label": "Qwen3.5-Flash", "thinking_default": "enabled",
     "can_disable": True, "efforts": [], "effort_default": "", "note": ""},
]

_MODEL_INDEX = {m["id"]: m for m in MODEL_CATALOG}

# 由弱到强，用于把不支持的档位就近收敛
EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
_EFFORT_RANK = {v: i for i, v in enumerate(EFFORT_ORDER)}

_OFF_WORDS = {"off", "disabled", "disable", "none", "false", "0", "no", "关闭"}
_ON_WORDS = {"enabled", "enable", "on", "true", "1", "yes"}


def model_spec(model_id: str) -> dict | None:
    """查模型档案；自定义（清单外）模型返回 None，按最宽松处理。"""
    return _MODEL_INDEX.get((model_id or "").strip())


def snap_reasoning_effort(effort: str, model_id: str) -> str:
    """把推理深度收敛到该模型支持的档位。

    清单外的模型不做收敛——文档没写不代表不支持，硬收敛反而可能挡住新模型。
    """
    if not effort:
        return ""
    spec = model_spec(model_id)
    if not spec:
        return effort
    allowed = spec.get("efforts") or []
    if not allowed or effort in allowed:
        return effort
    # 就近取：优先落在允许集合里最靠近的一档
    rank = _EFFORT_RANK.get(effort)
    if rank is None:
        return spec.get("effort_default") or allowed[-1]
    best = min(allowed, key=lambda v: abs(_EFFORT_RANK.get(v, 0) - rank))
    return best


def resolve_thinking(level: str | None, model_id: str) -> tuple[str, str]:
    """把「思考强度」解析成请求参数 `(thinking.type, reasoning_effort)`。

    reasoning_effort 为空串表示**不下发**该字段，交给模型默认值。

    强度取值：
      off            → thinking=disabled（模型强制思考时降级为最低档）
      low/medium/high→ thinking=enabled + reasoning_effort=<档位>
      auto/空        → 跟随模型默认思考行为，不下发 reasoning_effort
      enabled        → thinking=enabled，不下发 reasoning_effort
    """
    spec = model_spec(model_id) or {}
    raw = (level or "").strip().lower()

    if raw in {"", "auto", "default", "model"}:
        default = spec.get("thinking_default", "enabled")
        return ("disabled" if default == "disabled" else "enabled"), ""

    if raw in _OFF_WORDS:
        if spec.get("can_disable", True) is False:
            # 关不掉的模型：退回最低推理深度，而不是硬下发 disabled 让接口报错
            allowed = spec.get("efforts") or []
            lowest = (
                min(allowed, key=lambda v: _EFFORT_RANK.get(v, 0)) if allowed else "low"
            )
            return "enabled", lowest
        return "disabled", ""

    if raw in _ON_WORDS:
        return "enabled", ""

    if raw not in _EFFORT_RANK:
        raw = "medium"
    if spec.get("can_disable", True) is False and raw == "none":
        raw = "low"
    return "enabled", snap_reasoning_effort(raw, model_id)

# ---------------------------------------------------------------- 沙盒

SANDBOX_TIMEOUT = float(os.getenv("SANDBOX_TIMEOUT", "5"))
# 单个测试用例允许的最大 stdout 字符数，超出即截断，防止死循环刷爆内存
SANDBOX_MAX_OUTPUT_CHARS = int(os.getenv("SANDBOX_MAX_OUTPUT_CHARS", "20000"))
# 递归深度上限，防止爆栈
SANDBOX_RECURSION_LIMIT = int(os.getenv("SANDBOX_RECURSION_LIMIT", "3000"))

# ---------------------------------------------------------------- 评估

# Critic 判定为缺陷时，置信度低于该阈值的段落不计入“错误起始步骤”
CRITIC_CONFIDENCE_THRESHOLD = float(os.getenv("CRITIC_CONFIDENCE_THRESHOLD", "0.5"))
# 五个步骤的 Critic 调用彼此独立，可并发。设为 1 即回到串行。
# 关闭深度思考后单题已降到约 2 分钟，一般无需开启；若恢复思考模式可设 5。
CRITIC_PARALLELISM = max(1, int(os.getenv("CRITIC_PARALLELISM", "1")))

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
