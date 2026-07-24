#!/usr/bin/env python3
"""Run independent, read-only Codex review passes and consolidate the findings."""

import argparse
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from typing import Any, Dict, List, Optional, Sequence, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
LANE_SCHEMA = SCRIPT_DIR / "schemas" / "lane-review.json"
FINAL_SCHEMA = SCRIPT_DIR / "schemas" / "final-report.json"
SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
CONFIDENCE_VALUES = {"high", "medium", "low"}
EFFORT_VALUES = ("minimal", "low", "medium", "high", "xhigh")
THREAD_ID_PATTERN = re.compile(r"^[0-9a-fA-F-]{36}$")
FINDING_FIELDS = (
    "title",
    "severity",
    "category",
    "file",
    "line_start",
    "line_end",
    "summary",
    "evidence",
    "trigger",
    "suggested_fix",
    "suggested_test",
    "confidence",
)

REVIEW_LANES: Sequence[Tuple[str, str]] = (
    (
        "correctness",
        "业务正确性、行为回归、边界条件、空值、数据转换、控制流和跨文件调用关系",
    ),
    (
        "state-concurrency",
        "并发与异步、状态一致性、事务边界、生命周期、竞态、锁、重入和资源释放",
    ),
    (
        "security",
        "鉴权授权、输入验证、注入、敏感数据、路径与命令处理、信任边界和依赖风险",
    ),
    (
        "reliability-performance",
        "错误处理、失败恢复、兼容性、性能退化、复杂度、内存与句柄泄漏和可观测性",
    ),
    (
        "contracts-tests",
        "API 与数据契约、向后兼容、测试覆盖、断言质量以及缺失的回归测试场景",
    ),
)


class ReviewError(RuntimeError):
    """An expected review workflow failure."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="对一个已提交的 PR 分支执行多视角、只读 Codex Review。"
    )
    parser.add_argument("--repo", required=True, help="本地 Git 仓库路径")
    parser.add_argument(
        "--base",
        help="目标分支或引用；省略时先读取当前 GitHub PR，再检测远程默认分支",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=1,
        help="每个视角的独立审查次数；重要 PR 可设为 2（默认：1）",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=5,
        help="并发 Codex 任务数（默认：5）",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="让汇总任务重新验证代码并执行 gap search；更完整但更慢",
    )
    parser.add_argument(
        "--model",
        help="显式覆盖模型；默认严格继承调用此 Skill 的 Codex 会话模型",
    )
    parser.add_argument(
        "--effort",
        choices=EFFORT_VALUES,
        help="显式覆盖推理强度；默认严格继承调用此 Skill 的 Codex 会话推理强度",
    )
    parser.add_argument(
        "--fail-on",
        choices=tuple(SEVERITY_RANK),
        default="P2",
        help="发现该级别及以上问题时返回退出码 1（默认：P2）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="单个 Codex 任务超时秒数（默认：1800）",
    )
    parser.add_argument(
        "--output-dir",
        help="本次运行的输出目录；必须不存在，默认写入系统临时目录",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="允许工作区存在未提交改动；可能降低快照一致性，不推荐",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只检查仓库并显示将执行的 Codex 命令，不发起审查",
    )
    args = parser.parse_args()

    if args.passes < 1:
        parser.error("--passes 必须大于等于 1")
    if args.jobs < 1:
        parser.error("--jobs 必须大于等于 1")
    if args.timeout < 1:
        parser.error("--timeout 必须大于等于 1")
    return args


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise ReviewError("未找到命令：{}".format(name))


def resolve_session_execution() -> Tuple[str, str]:
    thread_id = os.environ.get("CODEX_THREAD_ID", "").strip()
    if not thread_id:
        raise ReviewError(
            "当前环境没有 CODEX_THREAD_ID，无法继承调用会话模型和推理强度；"
            "请显式传入 --model 和 --effort"
        )
    if not THREAD_ID_PATTERN.fullmatch(thread_id):
        raise ReviewError("CODEX_THREAD_ID 格式无效，拒绝推断模型")

    codex_home = Path(
        os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    ).expanduser()
    sessions_dir = codex_home / "sessions"
    if not sessions_dir.is_dir():
        raise ReviewError("找不到 Codex 会话目录：{}".format(sessions_dir))

    candidates = list(sessions_dir.rglob("*{}*.jsonl".format(thread_id)))
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for session_file in candidates:
        model = None
        effort = None
        try:
            with session_file.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") != "turn_context":
                        continue
                    payload = event.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    candidate = payload.get("model")
                    candidate_effort = payload.get("effort")
                    if (
                        isinstance(candidate, str)
                        and candidate.strip()
                        and candidate_effort in EFFORT_VALUES
                    ):
                        model = candidate.strip()
                        effort = candidate_effort
        except OSError:
            continue
        if model and effort:
            return model, effort

    raise ReviewError(
        "无法从当前 Codex 会话 {} 解析模型和推理强度；"
        "请显式传入 --model 和 --effort".format(thread_id)
    )


def resolve_execution(
    explicit_model: Optional[str], explicit_effort: Optional[str]
) -> Tuple[str, str]:
    model = explicit_model.strip() if explicit_model and explicit_model.strip() else None
    effort = explicit_effort if explicit_effort else None
    if model and effort:
        return model, effort
    session_model, session_effort = resolve_session_execution()
    return model or session_model, effort or session_effort


def run_command(
    command: Sequence[str],
    *,
    stdin: Optional[str] = None,
    timeout: Optional[int] = None,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(command),
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewError("命令执行超时：{}".format(shlex.join(command))) from exc
    except OSError as exc:
        raise ReviewError("命令无法执行：{}：{}".format(shlex.join(command), exc)) from exc


def git(repo: Path, *args: str, check: bool = True) -> str:
    command = ["git", "-C", str(repo)] + list(args)
    result = run_command(command)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReviewError("Git 命令失败：{}\n{}".format(shlex.join(command), detail))
    return result.stdout.strip()


def resolve_commit(repo: Path, ref: str) -> Optional[str]:
    value = git(
        repo,
        "rev-parse",
        "--verify",
        "--quiet",
        "{}^{{commit}}".format(ref),
        check=False,
    )
    return value or None


def choose_base_candidate(
    candidates: Sequence[Tuple[str, str]], source: str
) -> Dict[str, str]:
    unique = {ref: sha for ref, sha in candidates if ref and sha}
    if not unique:
        raise ReviewError("没有可用的目标分支候选")
    shas = set(unique.values())
    if len(unique) > 1 and len(shas) > 1:
        details = ", ".join(
            "{} ({})".format(ref, sha[:12]) for ref, sha in sorted(unique.items())
        )
        raise ReviewError("目标分支存在歧义，请显式传入 --base：{}".format(details))
    preferred = next((ref for ref in unique if ref.startswith("origin/")), None)
    selected_ref = preferred or sorted(unique)[0]
    return {
        "base_ref": selected_ref,
        "base_sha": unique[selected_ref],
        "base_source": source,
    }


def detect_github_pr_base(repo: Path) -> Optional[Dict[str, str]]:
    if shutil.which("gh") is None:
        return None
    result = run_command(
        ["gh", "pr", "view", "--json", "baseRefName,baseRefOid"],
        timeout=30,
        cwd=repo,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    base_name = payload.get("baseRefName")
    base_oid = payload.get("baseRefOid")
    if not isinstance(base_name, str) or not isinstance(base_oid, str):
        return None
    base_name = base_name.strip()
    base_oid = base_oid.strip()
    if not base_name or not base_oid:
        return None
    local_oid = resolve_commit(repo, base_oid)
    if not local_oid:
        raise ReviewError(
            "已从 GitHub PR 检测到目标分支 {}，但基准提交 {} 不在本地；请先更新对应远程引用"
            .format(base_name, base_oid)
        )
    return {
        "base_ref": base_name,
        "base_sha": local_oid,
        "base_source": "github-pr",
    }


def detect_remote_default_base(repo: Path) -> Dict[str, str]:
    remotes = [line for line in git(repo, "remote").splitlines() if line]
    remotes.sort(key=lambda remote: (remote != "origin", remote))
    candidates: List[Tuple[str, str]] = []
    for remote in remotes:
        full_ref = git(
            repo,
            "symbolic-ref",
            "--quiet",
            "refs/remotes/{}/HEAD".format(remote),
            check=False,
        )
        prefix = "refs/remotes/"
        if not full_ref.startswith(prefix):
            continue
        short_ref = full_ref[len(prefix) :]
        sha = resolve_commit(repo, short_ref)
        if sha:
            candidates.append((short_ref, sha))
    if candidates:
        return choose_base_candidate(candidates, "remote-default")

    remote_refs = git(
        repo,
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/remotes",
    ).splitlines()
    for ref in remote_refs:
        if ref.endswith("/main") or ref.endswith("/master"):
            sha = resolve_commit(repo, ref)
            if sha:
                candidates.append((ref, sha))
    if candidates:
        return choose_base_candidate(candidates, "remote-main-master")
    raise ReviewError(
        "无法从当前 GitHub PR 或本地远程默认分支确定目标分支，请显式传入 --base"
    )


def resolve_base(repo: Path, explicit_base: Optional[str]) -> Dict[str, str]:
    if explicit_base and explicit_base.strip():
        base_ref = explicit_base.strip()
        base_sha = resolve_commit(repo, base_ref)
        if not base_sha:
            raise ReviewError("目标分支或引用不存在：{}".format(base_ref))
        return {
            "base_ref": base_ref,
            "base_sha": base_sha,
            "base_source": "explicit",
        }
    github_base = detect_github_pr_base(repo)
    if github_base:
        return github_base
    return detect_remote_default_base(repo)


def collect_snapshot(repo_arg: str, base_ref: Optional[str]) -> Dict[str, Any]:
    requested_repo = Path(repo_arg).expanduser().resolve()
    if not requested_repo.is_dir():
        raise ReviewError("仓库目录不存在：{}".format(requested_repo))

    repo_root_text = git(requested_repo, "rev-parse", "--show-toplevel")
    repo_root = Path(repo_root_text).resolve()
    base = resolve_base(repo_root, base_ref)
    head_sha = git(repo_root, "rev-parse", "--verify", "HEAD^{commit}")
    base_sha = base["base_sha"]
    merge_base = git(repo_root, "merge-base", head_sha, base_sha)
    changed_files_text = git(repo_root, "diff", "--name-only", merge_base, head_sha, "--")
    status = git(repo_root, "status", "--porcelain=v1", "--untracked-files=all")

    return {
        "repo_root": str(repo_root),
        "repo_name": repo_root.name,
        "base_ref": base["base_ref"],
        "base_sha": base_sha,
        "base_source": base["base_source"],
        "head_sha": head_sha,
        "merge_base": merge_base,
        "changed_files": [line for line in changed_files_text.splitlines() if line],
        "worktree_status": status,
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def ensure_snapshot_unchanged(snapshot: Dict[str, Any], allow_dirty: bool) -> None:
    repo = Path(snapshot["repo_root"])
    current_head = git(repo, "rev-parse", "--verify", "HEAD^{commit}")
    if current_head != snapshot["head_sha"]:
        raise ReviewError(
            "审查期间 HEAD 发生变化：{} -> {}，结果已作废".format(
                snapshot["head_sha"], current_head
            )
        )

    current_status = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if not allow_dirty and current_status:
        raise ReviewError("审查期间工作区出现未提交改动，结果已作废")
    if allow_dirty and current_status != snapshot["worktree_status"]:
        raise ReviewError("审查期间工作区状态发生变化，结果已作废")


def codex_prefix(repo: Path, model: str, effort: str) -> List[str]:
    command = [
        "codex",
        "exec",
        "-C",
        str(repo),
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--model",
        model,
        "--config",
        "model_reasoning_effort={}".format(json.dumps(effort)),
    ]
    return command


def lane_prompt(
    lane_id: str, focus: str, snapshot: Dict[str, Any], pass_number: int
) -> str:
    return textwrap.dedent(
        """
        这是用户明确授权的只读本地代码审查。不得修改文件、分支、索引或提交。

        审查快照：
        - 解析后的基准：{base_ref}
        - 固定基准提交：{base_sha}
        - 固定 HEAD：{head_sha}
        - merge-base：{merge_base}
        - 独立轮次：{pass_number}

        仅审查 Git 对象中的固定范围 `{merge_base}..{head_sha}`。先读取该精确 diff，再读取必要的周边代码和测试来验证影响。
        不得把工作区未提交内容、其他提交或目标分支之后的变化纳入发现。
        当前唯一审查视角是：{focus}。

        要求：
        1. 逐文件检查，不要在发现第一个问题后停止。
        2. 只报告可触发、可操作的真实缺陷；不要报告纯风格、偏好或没有证据的猜测。
        3. 每项必须说明触发路径、代码证据、影响以及建议回归测试。
        4. P0=普遍且灾难性；P1=应阻断合并的高影响缺陷；P2=需要修复的普通缺陷；P3=低影响但真实的问题。
        5. 如果没有问题，findings 必须为空数组。
        6. lane 字段必须精确填写为 {lane_id}。
        """
    ).strip().format(
        base_ref=snapshot["base_ref"],
        base_sha=snapshot["base_sha"],
        head_sha=snapshot["head_sha"],
        merge_base=snapshot["merge_base"],
        pass_number=pass_number,
        focus=focus,
        lane_id=lane_id,
    )


def lane_command(
    repo: Path,
    output_file: Path,
    prompt: str,
    model: str,
    effort: str,
) -> List[str]:
    return codex_prefix(repo, model, effort) + [
        "--output-schema",
        str(LANE_SCHEMA),
        "--output-last-message",
        str(output_file),
        prompt,
    ]


def load_json_object(path: Path, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReviewError("{} 未生成输出文件：{}".format(label, path)) from exc
    except json.JSONDecodeError as exc:
        raise ReviewError("{} 输出不是有效 JSON：{}".format(label, exc)) from exc
    if not isinstance(value, dict):
        raise ReviewError("{} 输出必须是 JSON 对象".format(label))
    return value


def validate_finding(
    finding: Any, label: str, *, require_source_lanes: bool
) -> None:
    if not isinstance(finding, dict):
        raise ReviewError("{} 中的 finding 必须是 JSON 对象".format(label))
    missing = [field for field in FINDING_FIELDS if field not in finding]
    if missing:
        raise ReviewError("{} 中的 finding 缺少字段：{}".format(label, ", ".join(missing)))
    if finding["severity"] not in SEVERITY_RANK:
        raise ReviewError("{} 包含无效严重度：{}".format(label, finding["severity"]))
    if finding["confidence"] not in CONFIDENCE_VALUES:
        raise ReviewError("{} 包含无效置信度：{}".format(label, finding["confidence"]))
    if not isinstance(finding["line_start"], int) or not isinstance(
        finding["line_end"], int
    ):
        raise ReviewError("{} 的行号必须是整数".format(label))
    if finding["line_start"] < 1 or finding["line_end"] < finding["line_start"]:
        raise ReviewError("{} 包含无效行号范围".format(label))
    if require_source_lanes:
        source_lanes = finding.get("source_lanes")
        if not isinstance(source_lanes, list) or not source_lanes:
            raise ReviewError("{} 的 source_lanes 必须是非空数组".format(label))
        if not all(isinstance(item, str) and item for item in source_lanes):
            raise ReviewError("{} 的 source_lanes 包含无效值".format(label))


def validate_lane_payload(payload: Dict[str, Any], lane_id: str, label: str) -> None:
    if payload.get("lane") != lane_id:
        raise ReviewError(
            "{} 返回了错误 lane：{}，预期 {}".format(label, payload.get("lane"), lane_id)
        )
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ReviewError("{} 缺少 findings 数组".format(label))
    for index, finding in enumerate(findings, 1):
        validate_finding(
            finding, "{} finding #{}".format(label, index), require_source_lanes=False
        )


def validate_verified_payload(payload: Dict[str, Any]) -> None:
    findings = payload.get("findings")
    rejected = payload.get("rejected_candidates")
    if not isinstance(findings, list):
        raise ReviewError("汇总验证输出缺少 findings 数组")
    if not isinstance(rejected, list):
        raise ReviewError("汇总验证输出缺少 rejected_candidates 数组")
    if not isinstance(payload.get("gap_search_summary"), str):
        raise ReviewError("汇总验证输出缺少 gap_search_summary")
    for index, finding in enumerate(findings, 1):
        validate_finding(
            finding,
            "verified finding #{}".format(index),
            require_source_lanes=True,
        )
    for index, item in enumerate(rejected, 1):
        if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not isinstance(
            item.get("reason"), str
        ):
            raise ReviewError("rejected candidate #{} 格式无效".format(index))


def run_lane(
    task: Tuple[int, str, str],
    snapshot: Dict[str, Any],
    raw_dir: Path,
    log_dir: Path,
    model: str,
    effort: str,
    timeout: int,
) -> Dict[str, Any]:
    pass_number, lane_id, focus = task
    task_id = "{}-pass-{}".format(lane_id, pass_number)
    output_file = raw_dir / "{}.json".format(task_id)
    prompt = lane_prompt(lane_id, focus, snapshot, pass_number)
    command = lane_command(
        Path(snapshot["repo_root"]),
        output_file,
        prompt,
        model,
        effort,
    )

    print("[开始] {}".format(task_id), flush=True)
    result = run_command(command, timeout=timeout)
    (log_dir / "{}.stdout.log".format(task_id)).write_text(
        result.stdout, encoding="utf-8"
    )
    (log_dir / "{}.stderr.log".format(task_id)).write_text(
        result.stderr, encoding="utf-8"
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReviewError("{} 执行失败（退出码 {}）：{}".format(task_id, result.returncode, detail))

    payload = load_json_object(output_file, task_id)
    validate_lane_payload(payload, lane_id, task_id)
    print("[完成] {}：{} 个候选问题".format(task_id, len(payload["findings"])), flush=True)
    return {
        "task_id": task_id,
        "expected_lane": lane_id,
        "pass": pass_number,
        "result": payload,
    }


def verifier_prompt(snapshot: Dict[str, Any], deep: bool) -> str:
    if deep:
        return textwrap.dedent(
            """
            这是用户明确授权的只读本地代码审查汇总。不得修改任何文件或 Git 状态。

            固定审查范围是 {merge_base} 到 {head_sha}，固定基准提交为 {base_sha}。
            stdin 中包含多个互相独立的审查结果。

            请完成以下工作：
            1. 回到代码和差异中逐条验证候选问题，只保留有明确触发路径和代码证据的问题。
            2. 合并同一根因的重复问题，保留全部 source_lanes。
            3. 修正文件路径、行号、严重度和描述；拒绝纯风格、推测性或已由现有代码处理的问题。
            4. 做一次独立的 gap search，不要只验证输入候选；继续寻找候选列表遗漏的 P0-P3 真实缺陷。
            5. gap search 新发现的问题将 source_lanes 填为 ["gap-search"]。
            6. findings 按 P0、P1、P2、P3 排序；同级按影响范围排序。
            7. 如果确认没有问题，findings 返回空数组。
            """
        ).strip().format(
            merge_base=snapshot["merge_base"],
            head_sha=snapshot["head_sha"],
            base_sha=snapshot["base_sha"],
        )
    return textwrap.dedent(
        """
        这是快速模式的结构化审查汇总。stdin 中包含五个已经独立检查代码的审查结果。

        不要执行命令、调用工具、读取仓库或重新检查代码，也不要执行 gap search。仅处理 stdin 中的候选数据。

        请完成以下工作：
        1. 合并同一根因的重复候选，保留并合并全部 source_lanes。
        2. 保留所有非重复候选；不得因为没有重新读取代码而拒绝候选。
        3. 只根据候选现有证据统一标题、严重度、文件、行号和描述，不得编造新证据。
        4. rejected_candidates 仅记录重复项或候选数据内部明确自相矛盾的项目。
        5. findings 按 P0、P1、P2、P3 排序；同级按影响范围排序。
        6. gap_search_summary 填写“快速模式未执行代码复核或 gap search”。
        """
    ).strip()


def verifier_command(
    workdir: Path,
    output_file: Path,
    prompt: str,
    model: str,
    effort: str,
    skip_git_repo_check: bool,
) -> List[str]:
    command = codex_prefix(workdir, model, effort)
    if skip_git_repo_check:
        command.append("--skip-git-repo-check")
    return command + [
        "--output-schema",
        str(FINAL_SCHEMA),
        "--output-last-message",
        str(output_file),
        prompt,
    ]


def run_verifier(
    snapshot: Dict[str, Any],
    lane_results: Sequence[Dict[str, Any]],
    run_dir: Path,
    log_dir: Path,
    model: str,
    effort: str,
    timeout: int,
    deep: bool,
) -> Dict[str, Any]:
    output_file = run_dir / "verified-findings.json"
    prompt = verifier_prompt(snapshot, deep)
    workdir = Path(snapshot["repo_root"]) if deep else run_dir
    command = verifier_command(workdir, output_file, prompt, model, effort, not deep)
    verifier_payload: Dict[str, Any] = {"lane_results": lane_results}
    if deep:
        verifier_payload["snapshot"] = snapshot
    verifier_input = json.dumps(verifier_payload, ensure_ascii=False, indent=2)

    stage = "深度验证与 gap search" if deep else "快速合并与去重"
    print("[开始] {}".format(stage), flush=True)
    result = run_command(command, stdin=verifier_input, timeout=timeout)
    (log_dir / "verifier.stdout.log").write_text(result.stdout, encoding="utf-8")
    (log_dir / "verifier.stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReviewError("汇总验证失败（退出码 {}）：{}".format(result.returncode, detail))

    payload = load_json_object(output_file, "汇总验证")
    validate_verified_payload(payload)
    print("[完成] 汇总 {} 个问题".format(len(payload["findings"])), flush=True)
    return payload


def escape_table(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def count_severities(findings: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counts = {severity: 0 for severity in SEVERITY_RANK}
    for finding in findings:
        severity = finding.get("severity")
        if severity in counts:
            counts[severity] += 1
    return counts


def build_markdown(result: Dict[str, Any], run_dir: Path) -> str:
    snapshot = result["snapshot"]
    verified = result["review"]
    findings = verified["findings"]
    counts = result["summary"]["severity_counts"]
    lines = [
        "# Codex 本地多视角审查报告",
        "",
        "## 快照",
        "",
        "- 仓库：`{}`".format(snapshot["repo_root"]),
        "- 解析后的基准：`{}`".format(snapshot["base_ref"]),
        "- 基准来源：`{}`".format(snapshot["base_source"]),
        "- 基准提交：`{}`".format(snapshot["base_sha"]),
        "- HEAD：`{}`".format(snapshot["head_sha"]),
        "- merge-base：`{}`".format(snapshot["merge_base"]),
        "- 模型：`{}`".format(snapshot["model"]),
        "- 推理强度：`{}`".format(snapshot["effort"]),
        "- 变更文件数：{}".format(len(snapshot["changed_files"])),
        "- 审查任务数：{}".format(result["summary"]["review_tasks"]),
        "",
        "## 结论",
        "",
        "- 已确认问题：{}".format(len(findings)),
        "- P0：{}，P1：{}，P2：{}，P3：{}".format(
            counts["P0"], counts["P1"], counts["P2"], counts["P3"]
        ),
        "- 汇总模式：{}".format(result["summary"]["mode"]),
        "- 阻断阈值：{}".format(result["summary"]["fail_on"]),
        "- 流水线结果：{}".format("未通过" if result["summary"]["blocked"] else "通过"),
        "",
    ]

    if findings:
        lines.extend(
            [
                "## 问题列表",
                "",
                "| 级别 | 位置 | 标题 | 置信度 | 来源 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for finding in findings:
            location = "{}:{}".format(finding["file"], finding["line_start"])
            lines.append(
                "| {} | `{}` | {} | {} | {} |".format(
                    escape_table(finding["severity"]),
                    escape_table(location),
                    escape_table(finding["title"]),
                    escape_table(finding["confidence"]),
                    escape_table(", ".join(finding["source_lanes"])),
                )
            )
        lines.append("")

        for index, finding in enumerate(findings, 1):
            lines.extend(
                [
                    "### {}. [{}] {}".format(index, finding["severity"], finding["title"]),
                    "",
                    "- 位置：`{}:{}-{}`".format(
                        finding["file"], finding["line_start"], finding["line_end"]
                    ),
                    "- 分类：{}".format(finding["category"]),
                    "- 置信度：{}".format(finding["confidence"]),
                    "- 来源：{}".format(", ".join(finding["source_lanes"])),
                    "",
                    finding["summary"],
                    "",
                    "**证据**：{}".format(finding["evidence"]),
                    "",
                    "**触发方式**：{}".format(finding["trigger"]),
                    "",
                    "**建议修复**：{}".format(finding["suggested_fix"]),
                    "",
                    "**建议测试**：{}".format(finding["suggested_test"]),
                    "",
                ]
            )
    else:
        lines.extend(["未确认到 P0-P3 问题。", ""])

    lines.extend(
        [
            "## Gap search",
            "",
            verified["gap_search_summary"],
            "",
            "## 被拒绝的候选",
            "",
        ]
    )
    rejected = verified["rejected_candidates"]
    if rejected:
        for item in rejected:
            lines.append("- {}：{}".format(item["title"], item["reason"]))
    else:
        lines.append("无。")
    lines.extend(
        [
            "",
            "原始结果和日志位于：`{}`".format(run_dir),
            "",
        ]
    )
    return "\n".join(lines)


def make_run_dir(args: argparse.Namespace, snapshot: Dict[str, Any]) -> Path:
    if args.output_dir:
        run_dir = Path(args.output_dir).expanduser().resolve()
    else:
        timestamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        run_dir = (
            Path(tempfile.gettempdir())
            / "local-pr-review"
            / "runs"
            / snapshot["repo_name"]
            / "{}-{}-{}".format(timestamp, snapshot["head_sha"][:8], os.getpid())
        )
    if run_dir.exists():
        raise ReviewError("输出目录已经存在：{}".format(run_dir))
    run_dir.mkdir(parents=True)
    return run_dir


def print_dry_run(
    args: argparse.Namespace, snapshot: Dict[str, Any], model: str, effort: str
) -> None:
    repo = Path(snapshot["repo_root"])
    placeholder = Path("<output-dir>")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    print("\n将执行以下类型的命令：")
    for pass_number in range(1, args.passes + 1):
        for lane_id, focus in REVIEW_LANES:
            prompt = lane_prompt(lane_id, focus, snapshot, pass_number)
            command = lane_command(
                repo,
                placeholder / "raw" / "{}-pass-{}.json".format(lane_id, pass_number),
                prompt,
                model,
                effort,
            )
            print("- {}".format(shlex.join(command)))
    verifier_workdir = repo if args.deep else placeholder
    verify_command = verifier_command(
        verifier_workdir,
        placeholder / "verified-findings.json",
        verifier_prompt(snapshot, args.deep),
        model,
        effort,
        not args.deep,
    )
    print("- {}  # stdin: 各视角 JSON".format(shlex.join(verify_command)))


def execute(args: argparse.Namespace) -> int:
    require_command("git")
    require_command("codex")
    if not LANE_SCHEMA.is_file() or not FINAL_SCHEMA.is_file():
        raise ReviewError("JSON Schema 文件不完整，请重新安装工具")

    model, effort = resolve_execution(args.model, args.effort)
    snapshot = collect_snapshot(args.repo, args.base)
    snapshot["model"] = model
    snapshot["effort"] = effort
    if snapshot["worktree_status"] and not args.allow_dirty:
        raise ReviewError(
            "工作区存在未提交改动。请先处理改动，或明确使用 --allow-dirty（不推荐）"
        )

    if args.dry_run:
        print_dry_run(args, snapshot, model, effort)
        return 0

    run_dir = make_run_dir(args, snapshot)
    raw_dir = run_dir / "raw"
    log_dir = run_dir / "logs"
    raw_dir.mkdir()
    log_dir.mkdir()
    (run_dir / "snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not snapshot["changed_files"]:
        empty_review = {
            "findings": [],
            "rejected_candidates": [],
            "gap_search_summary": "基准提交与 HEAD 之间没有已提交差异，未启动 Codex Review。",
        }
        result = {
            "snapshot": snapshot,
            "summary": {
                "review_tasks": 0,
                "severity_counts": count_severities([]),
                "mode": "deep" if args.deep else "fast",
                "fail_on": args.fail_on,
                "blocked": False,
            },
            "review": empty_review,
        }
        (run_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (run_dir / "report.md").write_text(
            build_markdown(result, run_dir), encoding="utf-8"
        )
        print("没有已提交差异。报告：{}".format(run_dir / "report.md"))
        return 0

    tasks = [
        (pass_number, lane_id, focus)
        for pass_number in range(1, args.passes + 1)
        for lane_id, focus in REVIEW_LANES
    ]
    lane_results: List[Dict[str, Any]] = []
    failures: List[str] = []
    max_workers = min(args.jobs, len(tasks))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                run_lane,
                task,
                snapshot,
                raw_dir,
                log_dir,
                model,
                effort,
                args.timeout,
            ): task
            for task in tasks
        }
        for future in concurrent.futures.as_completed(future_map):
            task = future_map[future]
            try:
                lane_results.append(future.result())
            except Exception as exc:
                task_id = "{}-pass-{}".format(task[1], task[0])
                failures.append("{}：{}".format(task_id, exc))

    if failures:
        raise ReviewError("部分审查任务失败：\n- {}".format("\n- ".join(failures)))

    lane_results.sort(key=lambda item: (item["pass"], item["expected_lane"]))
    ensure_snapshot_unchanged(snapshot, args.allow_dirty)
    verified = run_verifier(
        snapshot,
        lane_results,
        run_dir,
        log_dir,
        model,
        effort,
        args.timeout,
        args.deep,
    )
    ensure_snapshot_unchanged(snapshot, args.allow_dirty)

    findings = verified["findings"]
    counts = count_severities(findings)
    threshold = SEVERITY_RANK[args.fail_on]
    blocked = any(
        SEVERITY_RANK.get(finding.get("severity", "P3"), 3) <= threshold
        for finding in findings
    )
    result = {
        "snapshot": snapshot,
        "summary": {
            "review_tasks": len(tasks) + 1,
            "severity_counts": counts,
            "mode": "deep" if args.deep else "fast",
            "fail_on": args.fail_on,
            "blocked": blocked,
        },
        "review": verified,
    }
    result_file = run_dir / "result.json"
    report_file = run_dir / "report.md"
    result_file.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_file.write_text(build_markdown(result, run_dir), encoding="utf-8")

    print("模型：{}".format(model))
    print("推理强度：{}".format(effort))
    print("汇总模式：{}".format("deep" if args.deep else "fast"))
    print(
        "审查完成：P0={} P1={} P2={} P3={}".format(
            counts["P0"], counts["P1"], counts["P2"], counts["P3"]
        )
    )
    print("报告：{}".format(report_file))
    print("结构化结果：{}".format(result_file))
    return 1 if blocked else 0


def main() -> int:
    args = parse_args()
    try:
        return execute(args)
    except ReviewError as exc:
        print("错误：{}".format(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
