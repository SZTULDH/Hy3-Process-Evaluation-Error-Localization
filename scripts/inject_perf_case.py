"""向 two_sum 题集注入大规模性能对抗用例。

暴力枚举 O(n^2) 在 n=10^4 上必然超时，而哈希表解法可在毫秒级返回 -1，
这个用例专门用于把“逻辑正确但复杂度不达标”的伪正确样本暴露出来。
该用例有 1 万个元素，不适合手写在 JSON 里，故由本脚本注入一次。
"""

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "datasets" / "code" / "medium" / "two_sum.json"
N = 10_000

data = json.loads(PATH.read_text(encoding="utf-8"))

perf_case = {
    "args": [[1] * N, 99999],
    "expected": -1,
    "timeout": 3,
    "note": "n=10^4 且无解，强制暴力解法跑满 O(n^2)",
}

# 幂等：已存在则先移除
data["adversarial_tests"] = [
    c for c in data["adversarial_tests"] if not c.get("note", "").startswith("n=10^4")
]
data["adversarial_tests"].append(perf_case)

# 性能用例放最后，避免它超时后连累后续用例无法执行
PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已注入性能用例：n={N}，two_sum 对抗用例数 = {len(data['adversarial_tests'])}")
