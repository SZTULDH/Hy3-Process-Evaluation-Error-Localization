"""过程评估工作台 —— 简易 Web 后端。

只依赖标准库：http.server 提供静态页与一个 POST 接口，评估逻辑全部复用
仓库里现成的 EvalPipeline（Producer → Checker 真实执行 → 规则信号 → Critic 分步审查）。

启动：
    python webapp/server.py            # 默认 http://127.0.0.1:8787
    python webapp/server.py 9000       # 指定端口
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import (  # noqa: E402
    CRITIC_PARALLELISM,
    DATASET_ROOT,
    HY3_API_KEY,
    HY3_BASE_URL,
    HY3_MODEL,
    HY3_THINKING,
    MODEL_CATALOG,
    SECTION_TITLES,
    model_spec,
)
from app.datasets import load_problem  # noqa: E402
from app.evaluator.pipeline import EvalPipeline, EvaluationResult  # noqa: E402
from app.evaluator.splitter import extract_code, split_sections  # noqa: E402
from app.llm.hy3 import Hy3LLM  # noqa: E402
from app.sandbox.runner import SuiteResult  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent

# 两种模式的评估重点，追加到题目描述之后，用来引导模型与 Critic 的关注点
ALGO_FOCUS = """\
## 评估重点（算法竞赛）
- 解法思路是否真正成立，是否与题目约束匹配，是否存在"看起来对但换组数据就错"的隐患。
- 复杂度分析必须写成 O(...) 形式，且与实际代码一致，不允许"声称 O(log n) 实际 O(n)"。
- 关键边界（空输入、单元素、重复、负数、极值、溢出等）必须逐一说明处理方式。
- 结论不以"公开测试通过"为准：对抗测试才是判断逻辑是否成立的依据。"""

CODE_FOCUS = """\
## 评估重点（代码任务）
- 重点判断**实现逻辑本身是否正确**，而不是仅仅看测试用例是否通过。
- 测试通过但逻辑存在隐患（特殊值未处理、状态未复位、异常路径缺失、副作用）的，仍应判为不成立。
- 第 2 段若复杂度不适用，请明确说明不适用及原因，但段落不可省略。
- 第 3 段必须覆盖需求里隐含的边界与异常输入，并说明代码如何处理。"""


# ------------------------------------------------------------------ 入参处理


def parse_tests(raw: Any) -> list[dict]:
    """解析测试用例输入。

    容忍三种写法：JSON 数组、JSON Lines（一行一个）、单个对象。
    每条归一化成 ``{"args": [...], "expected": ...}``。
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        text = (raw or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            items = []
            for line in text.splitlines():
                line = line.strip().rstrip(",")
                if not line:
                    continue
                items.append(json.loads(line))
        else:
            items = data if isinstance(data, list) else [data]

    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"测试用例必须是对象，收到: {type(item).__name__}")
        args = item.get("args", item.get("input", []))
        if not isinstance(args, list):
            args = [args]
        case = {"args": args, "expected": item.get("expected")}
        if "kwargs" in item:
            case["kwargs"] = item["kwargs"]
        out.append(case)
    return out


def normalize_candidate(raw: str) -> str:
    """用户粘贴**纯代码**时，包成带「代码实现」段的最小文档。

    不包的话 `split_sections` 切不出任何段落，Critic 拿到空内容后判
    "段落缺失" —— 代码明明执行全过，却被判不成立。这里补上段落外壳，
    让过程审查只在"确实没写思路"时才报缺失，而不是因为解析不出来。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if split_sections(text).get("代码实现") is not None:
        return text  # 本来就是分段的文档，原样使用
    if not extract_code(text):
        return text  # 不是代码（散文），包了也没用，交给后面如实报错
    return f"## 4. 代码实现\n\n```python\n{text}\n```"


def build_problem(payload: dict) -> dict:
    mode = payload.get("mode") or "algorithm"
    title = (payload.get("title") or "").strip()
    description = (payload.get("description") or "").strip()
    if not description:
        raise ValueError("题目描述 / 需求不能为空")

    focus = ALGO_FOCUS if mode == "algorithm" else CODE_FOCUS
    entry = (payload.get("entry_point") or "").strip()
    signature = (payload.get("function_signature") or "").strip()
    if not entry and signature:
        # 从 "def two_sum(nums, target):" 里抠函数名，省得用户重复填
        head = signature.strip()
        if head.startswith("def "):
            entry = head[4:].split("(")[0].strip()
    if not signature and entry:
        signature = f"def {entry}(...):"

    return {
        "id": title or f"gui-{mode}",
        "title": title or "GUI 输入",
        "difficulty": "custom",
        "description": f"{description}\n\n{focus}",
        "entry_point": entry,
        "function_signature": signature,
        "constraints": (payload.get("constraints") or "").strip() or "无特殊约束",
        "public_tests": payload.get("public_tests") or [],
        "adversarial_tests": payload.get("adversarial_tests") or [],
        "expected_complexity": payload.get("expected_complexity") or {},
        "required_edge_cases": payload.get("required_edge_cases") or [],
        "mode": mode,
    }


# ------------------------------------------------------------------ 题库

# 只在第一次请求题库时读盘；之后复用。题目详情更是按需单条读取——
# 50 题的正文 + 测试用例全量塞进页面没必要，也拖慢首屏。
_INDEX: dict | None = None


def scan_problems(force: bool = False) -> dict:
    """扫出题库索引（只含用于挑选的轻量字段）。

    逐文件容错：`app/datasets.load_all()` 一个坏 JSON 就整体崩，这里跳过坏
    文件并记录，页面照常能选其余题目。
    """
    global _INDEX
    if _INDEX is not None and not force:
        return _INDEX

    items: dict[str, dict] = {}
    broken: list[str] = []
    for path in sorted(DATASET_ROOT.rglob("*.json")):
        rel = str(path.relative_to(ROOT))
        try:
            data = load_problem(path)
        except Exception as exc:  # noqa: BLE001 - 坏文件不应拖垮整个题库
            broken.append(f"{rel}: {type(exc).__name__}: {exc}")
            continue
        pid = str(data.get("id") or path.stem)
        items[pid] = {
            "id": pid,
            "title": str(data.get("title") or path.stem),
            "difficulty": str(data.get("difficulty") or path.parent.name),
            "entry_point": str(data.get("entry_point") or ""),
            "path": rel,
            "n_public": len(data.get("public_tests") or []),
            "n_adv": len(data.get("adversarial_tests") or []),
            "has_mock": bool(data.get("mock_solution")),
        }

    _INDEX = {"items": items, "broken": broken}
    return _INDEX


def problem_index(difficulty: str = "", keyword: str = "") -> dict:
    idx = scan_problems()
    items = list(idx["items"].values())
    counts: dict[str, int] = {}
    for it in items:
        counts[it["difficulty"]] = counts.get(it["difficulty"], 0) + 1

    if difficulty:
        items = [i for i in items if i["difficulty"] == difficulty]
    if keyword:
        kw = keyword.lower()
        items = [
            i for i in items
            if kw in i["title"].lower() or kw in i["id"].lower()
            or kw in i["entry_point"].lower()
        ]
    items.sort(key=lambda i: (i["difficulty"], i["id"]))
    return {
        "total": len(items),
        "counts": counts,
        "problems": items,
        "broken": idx["broken"],
    }


def mock_solution_markdown(problem: dict) -> str:
    """把 `mock_solution`（分段 dict）拼成可直接粘进「候选解答」的五段文档。"""
    sol = problem.get("mock_solution") or {}
    if not isinstance(sol, dict) or not sol:
        return ""
    parts = []
    for n, title in enumerate(SECTION_TITLES, start=1):
        body = sol.get(title)
        if not body:
            continue
        parts.append(f"## {n}. {title}\n\n{body}")
    if not parts:  # 非标准键名（如英文键）时原样按 dict 顺序拼
        for n, (k, v) in enumerate(sol.items(), start=1):
            parts.append(f"## {n}. {k}\n\n{v}")
    return "\n\n".join(parts)


def problem_detail(problem_id: str) -> dict:
    idx = scan_problems()
    entry = idx["items"].get(problem_id)
    if entry is None:
        raise KeyError(problem_id)
    data = load_problem(ROOT / entry["path"])
    return {
        "problem": data,
        "candidate_md": mock_solution_markdown(data),
        "ground_truth": data.get("ground_truth"),
        "meta": entry,
    }


# ------------------------------------------------------------------ 评估


def _mock_llm():
    from app.llm.mock import MockLLM

    return MockLLM()


def build_llm(payload: dict) -> tuple[Any, dict]:
    """按请求里的模型名与思考强度构造 LLM，并返回运行参数快照。

    `model` 允许填清单外的自定义模型名；`effort` 为思考强度
    （off/low/medium/high/auto，也兼容旧的 enabled/disabled）。
    真正的参数展开（thinking.type + reasoning_effort）由 Hy3LLM 按模型能力完成。
    """
    if (payload.get("backend") or "hy3") != "hy3":
        return _mock_llm(), {"model": "mock", "thinking": "disabled", "effort": "",
                             "custom_model": False, "spec": None}

    model = (payload.get("model") or "").strip() or HY3_MODEL
    level = (payload.get("effort") or payload.get("thinking") or "auto")
    llm = Hy3LLM(model=model, thinking=level)
    return llm, {
        "model": llm.model,
        "thinking": llm.default_thinking,
        "effort": llm.default_reasoning_effort or "",
        "custom_model": model_spec(llm.model) is None,
        "spec": model_spec(llm.model),
    }


def run_evaluation(payload: dict) -> dict:
    started = time.perf_counter()
    problem = build_problem(payload)

    llm, run_cfg = build_llm(payload)
    pipeline = EvalPipeline(llm)
    candidate = normalize_candidate(payload.get("candidate"))
    code_only = bool(payload.get("code_only")) and bool(candidate)

    if candidate:
        result = pipeline.evaluate_solution(
            problem,
            candidate,
            only_sections=["代码实现"] if code_only else None,
        )
        source = "用户提供"
    else:
        result = pipeline.producer.produce(problem)
        result = pipeline.evaluate_solution(problem, result)
        source = "模型生成"

    data = result.to_dict()
    data.update(_meta(pipeline, llm, run_cfg, source, code_only, started,
                      problem.get("mode")))
    return data


def _reasoning_of(pipeline: EvalPipeline, thinking: str) -> str:
    """思考草稿：仅开启深度思考且模型确实返回时才非空。"""
    if thinking != "enabled":
        return ""
    try:
        return (pipeline.solver.last_reasoning or "")[:4000]
    except Exception:  # noqa: BLE001 - 思考内容缺失不影响主结果
        return ""


def _meta(pipeline: "EvalPipeline | None", llm: Any, run_cfg: dict, source: str,
          code_only: bool, started: float, mode: str) -> dict:
    """附加到结果上的运行元信息。"""
    return {
        "thinking": run_cfg.get("thinking", ""),
        "effort": run_cfg.get("effort", ""),
        "backend": "mock" if llm.__class__.__name__ == "MockLLM" else "hy3",
        "model": getattr(llm, "model", "mock"),
        "solution_source": source,
        "code_only": code_only,
        "reasoning": _reasoning_of(pipeline, run_cfg.get("thinking", "")),
        "wall_sec": round(time.perf_counter() - started, 2),
        "mode": mode,
    }


# ------------------------------------------------------------------ 流式


def _sse(event: str, data: Any) -> bytes:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    ).encode("utf-8")


# Agent 工作流的步骤中文名与归属，前端按此渲染
CHECKER_STEPS = {
    "parse": "① 解析解答 / 抽取代码",
    "public": "② 执行公开测试",
    "adversarial": "③ 执行对抗测试",
    "rules": "④ 规则层静态分析",
    "forensics": "⑤ 失败用例轨迹取证",
    "summary": "⑥ LLM 二次诊断总评",
}
AGENT_LABEL = {"checker": "Checker（执行取证 · 不靠 LLM 猜）",
               "critic": "Critic（逐段裁决 · LLM 软判断）"}


def _agent_event(agent: str, step: str, status: str, *, sec: float = 0.0,
                 inp: Any = None, out: Any = None, note: str = "") -> bytes:
    """推送一个 Agent 步骤事件，供前端还原两个 Agent 的工作流。"""
    return _sse("agent", {
        "agent": agent,
        "agent_label": AGENT_LABEL.get(agent, agent),
        "step": step,
        "title": CHECKER_STEPS.get(step, step) if agent == "checker" else step,
        "status": status,  # running | done | error
        "sec": round(sec, 2),
        "input": inp if inp is not None else {},
        "output": out if out is not None else {},
        "note": note,
    })


def stream_evaluation(payload: dict) -> Iterator[bytes]:
    """逐个阶段推送事件（SSE）。

    顺序：生成解答（逐字） → 沙盒执行（硬证据） → 分步审查（每段完成即推） → 汇总。
    Critic 的 5 次调用彼此独立，并发执行但按**完成顺序**推送，先算完的先显示。
    """
    started = time.perf_counter()
    problem = build_problem(payload)

    llm, run_cfg = build_llm(payload)
    thinking = run_cfg["thinking"]
    pipeline = EvalPipeline(llm)

    candidate = normalize_candidate(payload.get("candidate"))
    code_only = bool(payload.get("code_only")) and bool(candidate)

    yield _sse("start", {
        "model": run_cfg["model"],
        "thinking": thinking,
        "effort": run_cfg["effort"],
        "custom_model": run_cfg["custom_model"],
        "mode": problem.get("mode"),
        "title": problem.get("title"),
    })

    # ---- 1. 解答（流式）
    yield _sse("stage", {"name": "生成解答", "index": 1, "total": 4})
    if candidate:
        raw = candidate
        yield _sse("solution", {"text": raw, "source": "user"})
    else:
        for delta in pipeline.solver.iter_solve_deltas(problem):
            # 思考与正文分开推：前端各自渲染到不同区域
            yield _sse(
                "reasoning_delta" if delta.kind == "reasoning" else "delta",
                {"text": delta.text},
            )
        raw = pipeline.solver.last_text
        yield _sse("solution", {"text": raw, "source": "model"})

    # ---- 2. 执行与规则（无 LLM，出结果很快）
    yield _sse("stage", {"name": "沙盒执行 + 规则分析", "index": 2, "total": 4})
    t_step = time.perf_counter()
    steps: list[tuple[str, dict]] = []
    report = pipeline.checker.inspect(
        problem, raw, on_step=lambda s, io: steps.append((s, io))
    )
    step_sec = round((time.perf_counter() - t_step) / max(len(steps), 1), 2)
    for step, io in steps:
        yield _agent_event("checker", step, "done", sec=step_sec,
                           inp=io.get("input"), out=io.get("output"))
    yield _sse("tests", {
        "public": report.public.to_dict() if report.public else {},
        "adversarial": report.adversarial.to_dict() if report.adversarial else {},
        "findings": [f.to_dict() for f in report.findings],
        "code": report.code,
        "debug_hints": report.debug_hints,
    })

    # ---- 3. 分步审查
    yield _sse("stage", {"name": "分步审查", "index": 3, "total": 4})
    parsed = report.parsed or split_sections(raw)
    jobs = []
    for i, title in enumerate(SECTION_TITLES):
        if code_only and title != "代码实现":
            continue
        section = parsed.get(title)
        jobs.append((
            title,
            section.step_id if section else f"step_{i + 1}",
            section.content if section else "",
        ))

    verdicts: list[Any] = [None] * len(jobs)
    traces: list[dict] = [{} for _ in jobs]
    workers = min(CRITIC_PARALLELISM, len(jobs))

    def review(idx: int, title: str, step_id: str, content: str) -> Any:
        return pipeline.critic.review_section(
            section_title=title, step_id=step_id, section_content=content,
            problem=problem, code=report.code, signals=report.signals,
            rule_findings=report.findings, trace=traces[idx],
        )

    # 先把每段"待审查"推给前端，再跑——用户能看到 Critic 正在审哪一步
    for i, (title, step_id, content) in enumerate(jobs):
        yield _agent_event(
            "critic", f"段{i + 1}·{title}", "running",
            inp={
                "步骤": title,
                "step_id": step_id,
                "段落字符数": len(content or ""),
                "段落预览": (content or "（缺失）")[:200],
                "规则命中": [
                    f.error_type for f in report.findings if f.section == title
                ],
            },
        )

    def crit_done(i: int, v: Any) -> bytes:
        tr = traces[i]
        return _agent_event(
            "critic", f"段{i + 1}·{v.section}", "done", sec=tr.pop("_sec", 0.0),
            inp={
                "prompt": tr.get("prompt", ""),
                "system_prompt": tr.get("system_prompt", ""),
                "规则类型": tr.get("rule_types", []),
                "段落字符数": tr.get("section_chars", 0),
            },
            out={
                "verdict": v.verdict,
                "llm_verdict": v.llm_verdict,
                "confidence": v.confidence,
                "error_types": [str(t) for t in v.error_types],
                "reason": v.reason,
                "agreement": v.agreement,
                "llm_raw": tr.get("llm_raw", ""),
                "error": tr.get("error", ""),
            },
        )

    if workers <= 1:
        for i, (title, step_id, content) in enumerate(jobs):
            t0 = time.perf_counter()
            v = review(i, title, step_id, content)
            traces[i]["_sec"] = time.perf_counter() - t0
            verdicts[i] = v
            yield _sse("section", v.to_dict())
            yield crit_done(i, v)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(review, i, t, s, c): i
                for i, (t, s, c) in enumerate(jobs)
            }
            for fut in as_completed(future_map):
                i = future_map[fut]
                try:
                    v = fut.result()
                except Exception as exc:  # noqa: BLE001 - 单段失败不应中断
                    yield _agent_event("critic", f"段{i + 1}", "error",
                                       note=f"{type(exc).__name__}: {exc}")
                    yield _sse("section_error", {"index": i, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                verdicts[i] = v
                yield _sse("section", v.to_dict())
                yield crit_done(i, v)

    # ---- 4. 汇总
    yield _sse("stage", {"name": "汇总结论", "index": 4, "total": 4})

    # Checker 的第 ⑥ 步（LLM 二次诊断）放在最后：它要吃完整的执行证据
    yield _agent_event("checker", "summary", "running",
                       inp={"说明": "待执行证据齐备后调用 LLM 做二次诊断"})
    t_sum = time.perf_counter()
    sum_trace: dict = {}
    report.llm_summary = pipeline.checker.summarize(problem, report, trace=sum_trace)
    sum_sec = time.perf_counter() - t_sum
    yield _agent_event(
        "checker", "summary", "done", sec=sum_sec,
        inp=sum_trace.get("input", {}),
        out={
            "summary": report.llm_summary.get("summary", ""),
            "root_cause": report.llm_summary.get("root_cause", ""),
            "pseudo_correct": report.llm_summary.get("pseudo_correct"),
            "process_ok": report.llm_summary.get("process_ok"),
            "llm_raw": sum_trace.get("llm_raw", ""),
            "error": sum_trace.get("error", ""),
        },
    )

    result = EvaluationResult(
        problem_id=problem.get("id", "unknown"),
        difficulty=problem.get("difficulty", "unknown"),
        title=problem.get("title", ""),
        raw_solution=raw,
        sections=parsed.as_dict(),
        code=report.code,
        public=report.public or SuiteResult(),
        adversarial=report.adversarial or SuiteResult(),
        rule_findings=report.findings,
        section_verdicts=[v for v in verdicts if v is not None],
        elapsed_sec=time.perf_counter() - started,
        backend="mock" if (payload.get("backend") or "hy3") != "hy3" else "hy3",
        checker_report=report.to_dict(),
        multi_agent=True,
    )
    pipeline._aggregate(result)
    result.checker_report = report.to_dict()

    data = result.to_dict()
    data.update(_meta(pipeline, llm, run_cfg,
                      "用户提供" if candidate else "模型生成",
                      code_only, started, problem.get("mode")))
    yield _sse("done", data)


# ------------------------------------------------------------------ HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "Hy3Workbench/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- 通用

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj: dict) -> None:
        self._send(
            code,
            json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    # -- 路由

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs, unquote, urlparse

        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if path in ("/", "/index.html"):
            file = STATIC_DIR / "index.html"
            if not file.exists():
                self._send_json(404, {"error": "index.html 缺失"})
                return
            self._send(200, file.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/config":
            self._send_json(200, config_payload())
        elif path == "/api/problems":
            # 只回索引（轻量字段），题面正文等选中后再单条取
            self._send_json(200, problem_index(
                unquote(query.get("difficulty", "")),
                unquote(query.get("q", "")),
            ))
        elif path == "/api/problem":
            pid = unquote(query.get("id", ""))
            if not pid:
                self._send_json(400, {"error": "缺少 id 参数"})
                return
            try:
                self._send_json(200, problem_detail(pid))
            except KeyError:
                self._send_json(404, {"error": f"题库里没有这道题: {pid}"})
        else:
            self._send_json(404, {"error": f"未找到: {path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path not in ("/api/evaluate", "/api/evaluate/stream"):
            self._send_json(404, {"error": "未找到"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send_json(400, {"error": f"请求体不是合法 JSON: {exc}"})
            return

        try:
            payload["public_tests"] = parse_tests(payload.get("public_tests"))
            payload["adversarial_tests"] = parse_tests(payload.get("adversarial_tests"))
        except Exception as exc:  # noqa: BLE001
            self._send_json(400, {"error": f"测试用例解析失败: {exc}"})
            return

        if path == "/api/evaluate/stream":
            self._send_stream(payload)
            return

        try:
            self._send_json(200, run_evaluation(payload))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            self._send_json(
                500,
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc()[-2000:],
                },
            )

    def _send_stream(self, payload: dict) -> None:
        """SSE 流式响应。

        不设 Content-Length，靠连接关闭标志结束；每帧写完立即 flush，
        否则中间代理会把整条流攒到最后才吐出来。
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            for frame in stream_evaluation(payload):
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return  # 用户中途关页面/取消，正常现象
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            try:
                self.wfile.write(_sse("error", {
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc()[-2000:],
                }))
                self.wfile.flush()
            except Exception:  # noqa: BLE001
                pass


def config_payload() -> dict:
    """前端据此渲染「模型 + 思考强度」两个选择器。"""
    return {
        "model": HY3_MODEL,
        "base_url": HY3_BASE_URL,
        "thinking_default": HY3_THINKING,
        "has_key": bool(HY3_API_KEY),
        "sections": list(SECTION_TITLES),
        "models": [
            {
                "id": m["id"],
                "label": m["label"],
                "can_disable": m["can_disable"],
                "efforts": m["efforts"],
                "effort_default": m["effort_default"],
                "thinking_default": m["thinking_default"],
                "note": m["note"],
            }
            for m in MODEL_CATALOG
        ],
    }


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    cfg = config_payload()
    print(f"过程评估工作台  ->  http://127.0.0.1:{port}")
    print(f"  模型     : {cfg['model']}")
    print(f"  接口     : {cfg['base_url']}")
    print(f"  API Key  : {'已配置' if cfg['has_key'] else '缺失！请在仓库根 .env 写入 HY3_API_KEY'}")
    print(f"  思考默认 : {cfg['thinking_default']}（off/low/medium/high/auto，可在页面上改）")
    print(f"  可选模型 : {len(cfg['models'])} 个（页面可切换或手填自定义）")
    print("Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
