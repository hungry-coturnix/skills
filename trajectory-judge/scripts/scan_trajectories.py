#!/usr/bin/env python3
"""
Trajectory Scanner and Diagnostic Extractor for the `trajectory-judge` skill.

Scans Antigravity conversation transcripts (`transcript.jsonl` and `transcript_full.jsonl`)
under `<appDataDir>/brain/` to locate all trajectories where a target skill was used,
maps parent-subagent hierarchies, flags LLM execution anomalies (failed tool calls,
redundant reads, inline Python one-liners, context compression events, user corrections,
subagent orchestration issues), and allows targeted inspection of steps without bloating context.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_BRAIN_DIR = Path(r"C:\Users\Tilman\.gemini\antigravity\brain")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)

READ_TOOLS = {"read_file", "view_file", "view_file_outline", "view_code_item"}
WRITE_TOOLS = {"write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content"}


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    steps = []
    if not path.exists():
        return steps
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                obj["_line_num"] = line_num
                steps.append(obj)
            except json.JSONDecodeError:
                continue
    return steps


def unescape_arg(val: Any) -> str:
    if val is None:
        return ""
    s = str(val)
    if s.startswith('"') and s.endswith('"'):
        try:
            return json.loads(s)
        except Exception:
            return s.strip('"')
    return s


def extract_path_arg(clean_args: Dict[str, str]) -> str:
    for key in ("Path", "AbsolutePath", "TargetFile", "File", "DirectoryPath", "SearchPath"):
        if key in clean_args and clean_args[key]:
            return clean_args[key]
    return ""


def extract_user_request(content: str) -> str:
    if not content:
        return ""
    m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", content, re.DOTALL)
    if m:
        return m.group(1).strip()
    return content.strip()


def is_routine_photo_input(text: str) -> bool:
    """Checks if a user message is simply supplying a photo filename."""
    t = text.strip()
    return bool(re.match(r"^[\w-]+\.(jpg|jpeg|png|heic|webp|tif|tiff)$", t, re.IGNORECASE))


def analyze_conversation(conv_dir: Path) -> Optional[Dict[str, Any]]:
    conv_id = conv_dir.name
    log_dir = conv_dir / ".system_generated" / "logs"
    transcript_path = log_dir / "transcript.jsonl"
    if not transcript_path.exists():
        return None

    steps = load_jsonl(transcript_path)
    if not steps:
        return None

    user_inputs: List[Dict[str, Any]] = []
    tool_counts: Counter = Counter()
    errors: List[Dict[str, Any]] = []
    truncated_steps: List[Dict[str, Any]] = []
    subagents_spawned: List[Dict[str, Any]] = []
    skills_read: Set[str] = set()
    skills_edited: Set[str] = set()
    skills_script_run: Set[str] = set()
    skills_mentioned: Set[str] = set()
    files_read_counts: Counter = Counter()
    files_edited_counts: Counter = Counter()
    inline_python_calls: List[Dict[str, Any]] = []
    polling_calls: List[Dict[str, Any]] = []
    compression_events: List[Dict[str, Any]] = []

    peak_context_tokens = 0
    first_context_tokens = 0
    prev_context_tokens = 0
    total_output_tokens = 0
    created_at = steps[0].get("created_at", "")
    ended_at = steps[-1].get("created_at", "")

    last_planner_tools: List[Dict[str, Any]] = []

    for step in steps:
        idx = step.get("step_index", step.get("_line_num", 0))
        stype = step.get("type", "")
        status = step.get("status", "")
        content = step.get("content", "") or ""
        trunc = step.get("truncated_fields", [])

        if trunc:
            truncated_steps.append({
                "step_index": idx,
                "line_num": step.get("_line_num"),
                "type": stype,
                "truncated_fields": trunc,
            })

        if stype == "USER_INPUT":
            req = extract_user_request(content)
            user_inputs.append({"step_index": idx, "content": req[:600]})
            for m in re.finditer(r"skills[/\\]([a-zA-Z0-9_-]+)[/\\]SKILL\.md", content, re.IGNORECASE):
                skills_mentioned.add(m.group(1).lower())
            for m in re.finditer(r"`([a-z0-9]+-[a-z0-9-]+)`", req):
                skills_mentioned.add(m.group(1).lower())

        elif stype == "PLANNER_RESPONSE":
            in_tok = step.get("input_tokens", 0) or 0
            cache_tok = step.get("cache_read_tokens", 0) or 0
            ctx_tok = in_tok + cache_tok
            out_tok = step.get("output_tokens", 0) or 0
            if ctx_tok > 0:
                if first_context_tokens == 0:
                    first_context_tokens = ctx_tok
                if ctx_tok > peak_context_tokens:
                    peak_context_tokens = ctx_tok
                # True context compression / history truncation event: total context tokens drop significantly
                if prev_context_tokens > 0 and (prev_context_tokens - ctx_tok) > 10000 and ctx_tok < prev_context_tokens * 0.85:
                    compression_events.append({
                        "step_index": idx,
                        "before_context_tokens": prev_context_tokens,
                        "after_context_tokens": ctx_tok,
                        "drop": prev_context_tokens - ctx_tok,
                    })
                prev_context_tokens = ctx_tok
            total_output_tokens += out_tok

            tcalls = step.get("tool_calls", []) or []
            last_planner_tools = tcalls
            for tc in tcalls:
                tname = tc.get("name", "unknown")
                targs = tc.get("args", {}) or {}
                tool_counts[tname] += 1

                clean_args = {k: unescape_arg(v) for k, v in targs.items()}
                path_arg = extract_path_arg(clean_args)

                if tname in READ_TOOLS and path_arg:
                    norm_path = os.path.normpath(path_arg)
                    files_read_counts[norm_path] += 1
                    m = re.search(r"skills[/\\]([a-zA-Z0-9_-]+)[/\\]SKILL\.md", norm_path, re.IGNORECASE)
                    if m:
                        skills_read.add(m.group(1).lower())
                elif tname in WRITE_TOOLS and path_arg:
                    norm_path = os.path.normpath(path_arg)
                    files_edited_counts[norm_path] += 1
                    m = re.search(r"skills[/\\]([a-zA-Z0-9_-]+)[/\\]", norm_path, re.IGNORECASE)
                    if m:
                        skills_edited.add(m.group(1).lower())

                if tname == "run_command":
                    cmd = clean_args.get("CommandLine", "")
                    for m in re.finditer(r"skills[/\\]([a-zA-Z0-9_-]+)[/\\]", cmd, re.IGNORECASE):
                        skills_script_run.add(m.group(1).lower())
                    if re.search(r"python(?:3|\.exe)?\s+-c\b", cmd):
                        inline_python_calls.append({"step_index": idx, "command": cmd[:200]})

                if tname in ("invoke_subagent", "browser_subagent"):
                    prompt_arg = clean_args.get("Prompt", "") or clean_args.get("Task", "")
                    role_arg = clean_args.get("Role", "") or clean_args.get("TaskName", "")
                    for m in re.finditer(r"skills[/\\]([a-zA-Z0-9_-]+)", prompt_arg, re.IGNORECASE):
                        skills_mentioned.add(m.group(1).lower())
                    for m in re.finditer(r"`([a-z0-9]+-[a-z0-9-]+)`", prompt_arg):
                        skills_mentioned.add(m.group(1).lower())
                    subagents_spawned.append({
                        "step_index": idx,
                        "role": role_arg,
                        "type_name": clean_args.get("TypeName", tname),
                        "prompt_preview": prompt_arg[:300],
                        "child_id": None,
                    })

                if tname in ("manage_task", "manage_subagents", "command_status"):
                    action = clean_args.get("Action", "status")
                    if action in ("status", "list"):
                        polling_calls.append({"step_index": idx, "tool": tname, "action": action})

        # Check tool output / generic steps for errors and subagent IDs
        if status == "ERROR" or "Command exited with code " in content:
            exit_match = re.search(r"Command exited with code (\d+)", content)
            if status == "ERROR" or (exit_match and exit_match.group(1) != "0"):
                errors.append({
                    "step_index": idx,
                    "status": status,
                    "exit_code": exit_match.group(1) if exit_match else None,
                    "preview": content[:350],
                })
        elif re.search(r"(Traceback \(most recent call last\)|ParserError|SyntaxError|Exception:|Error:)", content):
            if stype == "GENERIC" and ("Command output:" in content or "Error" in content[:120]):
                errors.append({
                    "step_index": idx,
                    "status": status,
                    "exit_code": "0_with_error_text",
                    "preview": content[:350],
                })

        # Match subagent conversation IDs returned by invoke_subagent
        if stype == "GENERIC" and last_planner_tools:
            if any(tc.get("name") in ("invoke_subagent", "browser_subagent") for tc in last_planner_tools):
                uuids = UUID_RE.findall(content)
                for u in uuids:
                    for sub in subagents_spawned:
                        if sub["child_id"] is None and sub["step_index"] < idx:
                            sub["child_id"] = u.lower()
                            break

    repeated_reads = {p: c for p, c in files_read_counts.most_common(15) if c > 1}
    repeated_edits = {p: c for p, c in files_edited_counts.most_common(10) if c > 2}

    return {
        "conversation_id": conv_id,
        "created_at": created_at,
        "ended_at": ended_at,
        "total_steps": len(steps),
        "first_context_tokens": first_context_tokens,
        "peak_context_tokens": peak_context_tokens,
        "total_output_tokens": total_output_tokens,
        "compression_events": compression_events,
        "user_inputs": user_inputs,
        "skills_read": sorted(skills_read),
        "skills_edited": sorted(skills_edited),
        "skills_script_run": sorted(skills_script_run),
        "skills_mentioned": sorted(skills_mentioned),
        "tool_counts": dict(tool_counts),
        "errors": errors,
        "truncated_steps": truncated_steps,
        "subagents_spawned": subagents_spawned,
        "repeated_reads": repeated_reads,
        "repeated_edits": repeated_edits,
        "inline_python_calls": inline_python_calls,
        "polling_calls": polling_calls,
    }


def classify_trajectory(info: Dict[str, Any], skill_name: str, parent_id: Optional[str]) -> str:
    """
    Classifies how a conversation interacted with `skill_name`:
    - 'EXECUTED': Read/ran the skill to perform a task (and did not edit skills).
    - 'SKILL_AUTHORING_OR_META': Created, critiqued, edited, or judged a skill.
    - 'DELEGATED_OR_MENTIONED': Referenced or delegated the skill without reading its SKILL.md directly.
    """
    sname = skill_name.lower()
    if "trajectory-judge" in info["skills_read"] or "trajectory-judge" in info["skills_edited"] or "trajectory-judge" in info["skills_script_run"]:
        if sname != "trajectory-judge":
            return "SKILL_AUTHORING_OR_META"

    if info["skills_edited"]:
        return "SKILL_AUTHORING_OR_META"

    first_prompt = info["user_inputs"][0]["content"].lower() if info["user_inputs"] else ""
    if re.search(r"(create|update|modify|edit|judge|evaluate|write)\s+(a\s+|an\s+|the\s+)?skill", first_prompt):
        return "SKILL_AUTHORING_OR_META"

    if sname in info["skills_read"] or sname in info["skills_script_run"]:
        return "EXECUTED"

    if sname in info["skills_mentioned"]:
        return "DELEGATED_OR_MENTIONED"

    return "INDIRECT"


def print_conversation_summary(r: Dict[str, Any]) -> None:
    cid = r["conversation_id"]
    pid = r.get("parent_conversation_id")
    cls = r.get("classification")
    print(f"----------------------------------------------------------------------")
    print(f"Conversation ID : {cid}")
    if pid:
        print(f"Parent Conv ID  : {pid} (Subagent Trajectory)")
    if cls:
        print(f"Classification  : {cls}")
    print(f"Timestamps      : {r['created_at']} -> {r['ended_at']}")
    print(f"Steps & Context : {r['total_steps']} steps | Context tokens (in+cache): {r['first_context_tokens']} -> {r['peak_context_tokens']} | Output tokens: {r['total_output_tokens']}")
    print(f"Skills Read     : {r['skills_read']} | Scripts Run: {r['skills_script_run']} | Skills Edited: {r['skills_edited']}")
    print(f"Tool Counts     : {json.dumps(r['tool_counts'])}")

    non_photo_inputs = [u for u in r["user_inputs"] if not is_routine_photo_input(u["content"])]
    photo_inputs_count = len(r["user_inputs"]) - len(non_photo_inputs)
    print(f"User Turns      : {len(r['user_inputs'])} total ({len(non_photo_inputs)} substantive / {photo_inputs_count} single-photo filename turns)")
    for ui in non_photo_inputs[:25]:
        preview = ui["content"].replace("\n", " ").replace("\r", "")[:200]
        print(f"  - [Step {ui['step_index']}]: {preview}")
    if len(non_photo_inputs) > 25:
        print(f"  ... ({len(non_photo_inputs) - 25} more substantive user turns)")

    print(f"Anomalies & Red Flags:")
    print(f"  - Errors / Failed Commands   : {len(r['errors'])} (steps: {[e['step_index'] for e in r['errors'][:30]]})")
    print(f"  - Inline `python -c` Calls   : {len(r['inline_python_calls'])} (steps: {[p['step_index'] for p in r['inline_python_calls'][:30]]})")
    print(f"  - Context Compression Drops  : {len(r['compression_events'])} {r['compression_events']}")
    print(f"  - Top Repeated File Reads    : {json.dumps(r['repeated_reads'])}")
    print(f"  - Top Repeated File Edits    : {json.dumps(r['repeated_edits'])}")
    print(f"  - Truncated Steps            : {len(r['truncated_steps'])} (steps: {[t['step_index'] for t in r['truncated_steps'][:25]]})")
    print(f"  - Polling / Status Calls     : {len(r['polling_calls'])} (steps: {[p['step_index'] for p in r['polling_calls'][:25]]})")
    if r["subagents_spawned"]:
        print(f"  - Subagents Spawned          : {len(r['subagents_spawned'])}")
        for sub in r["subagents_spawned"][:15]:
            print(f"      * [Step {sub['step_index']}] Role='{sub['role']}' -> Child ID: {sub['child_id']}")
        if len(r["subagents_spawned"]) > 15:
            print(f"      ... ({len(r['subagents_spawned']) - 15} more subagents spawned)")
    print()


def cmd_discover(args: argparse.Namespace) -> None:
    brain_dir = Path(args.brain_dir)
    skill_name = args.skill.lower().strip()

    all_convs: Dict[str, Dict[str, Any]] = {}
    parent_map: Dict[str, str] = {}  # child_id -> parent_id

    for conv_dir in sorted(brain_dir.iterdir()):
        if not conv_dir.is_dir():
            continue
        info = analyze_conversation(conv_dir)
        if info:
            cid = info["conversation_id"].lower()
            all_convs[cid] = info
            for sub in info["subagents_spawned"]:
                if sub["child_id"]:
                    parent_map[sub["child_id"].lower()] = cid

    matched_ids: Set[str] = set()
    for cid, info in all_convs.items():
        if (
            skill_name in info["skills_read"]
            or skill_name in info["skills_edited"]
            or skill_name in info["skills_script_run"]
            or skill_name in info["skills_mentioned"]
        ):
            matched_ids.add(cid)
            if cid in parent_map:
                matched_ids.add(parent_map[cid])

    sorted_cids = sorted(matched_ids, key=lambda c: all_convs[c]["created_at"])

    results = []
    for cid in sorted_cids:
        info = all_convs[cid]
        pid = parent_map.get(cid)
        role_class = classify_trajectory(info, skill_name, pid)
        if args.executed_only and role_class not in ("EXECUTED", "DELEGATED_OR_MENTIONED"):
            continue
        info["parent_conversation_id"] = pid
        info["classification"] = role_class
        results.append(info)

    if args.json:
        print(json.dumps(results, indent=2))
        return

    print(f"=== Trajectory Discovery Report for Skill: `{skill_name}` ===")
    print(f"Total matching trajectories: {len(results)}\n")

    for r in results:
        print_conversation_summary(r)


def parse_step_filter(spec: str) -> Set[int]:
    indices: Set[int] = set()
    if not spec:
        return indices
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            indices.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            indices.add(int(part))
    return indices


def cmd_inspect(args: argparse.Namespace) -> None:
    brain_dir = Path(args.brain_dir)
    conv_dir = brain_dir / args.conversation_id
    if not conv_dir.exists():
        print(f"Error: Conversation directory not found: {conv_dir}", file=sys.stderr)
        sys.exit(1)

    if args.summary:
        info = analyze_conversation(conv_dir)
        if info:
            print(f"=== Trajectory Diagnostic Summary: {args.conversation_id} ===")
            print_conversation_summary(info)
        else:
            print(f"Warning: Could not analyze conversation summary for {args.conversation_id}", file=sys.stderr)
        if not (args.steps or args.errors_only or args.planner_only):
            return

    log_dir = conv_dir / ".system_generated" / "logs"
    fname = "transcript_full.jsonl" if args.full else "transcript.jsonl"
    tpath = log_dir / fname
    if not tpath.exists():
        tpath = log_dir / "transcript.jsonl"
    if not tpath.exists():
        print(f"Error: Transcript not found for {args.conversation_id}", file=sys.stderr)
        sys.exit(1)

    steps = load_jsonl(tpath)
    step_filter = parse_step_filter(args.steps) if args.steps else set()
    max_chars = args.max_chars

    print(f"=== Inspecting Conversation: {args.conversation_id} ({tpath.name}, {len(steps)} steps) ===\n")

    for step in steps:
        idx = step.get("step_index", step.get("_line_num", 0))
        if step_filter and idx not in step_filter:
            continue

        stype = step.get("type", "")
        status = step.get("status", "")
        content = step.get("content", "") or ""
        thinking = step.get("thinking", "") or ""
        tcalls = step.get("tool_calls", []) or []
        trunc = step.get("truncated_fields", [])

        if args.errors_only:
            is_err = (
                status == "ERROR"
                or ("Command exited with code " in content and "Command exited with code 0" not in content)
                or re.search(r"(Traceback \(most recent call last\)|ParserError|SyntaxError|Exception:)", content)
            )
            if not is_err:
                continue

        if args.planner_only and stype not in ("PLANNER_RESPONSE", "USER_INPUT"):
            continue

        print(f"--- [Step {idx} | Line {step.get('_line_num')}] Type: {stype} | Status: {status} ---")
        if step.get("input_tokens") or step.get("output_tokens") or step.get("cache_read_tokens"):
            in_t = step.get("input_tokens", 0) or 0
            ca_t = step.get("cache_read_tokens", 0) or 0
            out_t = step.get("output_tokens", 0) or 0
            print(f"Tokens: context={in_t + ca_t} (in={in_t}, cache={ca_t}), out={out_t}")
        if trunc:
            print(f"Truncated Fields in compact log: {trunc} (use --full to view untruncated)")

        if thinking and (args.thinking or step_filter):
            t_disp = thinking if len(thinking) <= max_chars else thinking[:max_chars] + f"\n... [truncated {len(thinking) - max_chars} chars]"
            print(f"[THINKING]:\n{t_disp}")

        if tcalls:
            print("[TOOL CALLS]:")
            for tc in tcalls:
                clean_args = {k: unescape_arg(v) for k, v in (tc.get("args") or {}).items()}
                print(f"  - {tc.get('name')}: {json.dumps(clean_args, ensure_ascii=False)[:max_chars]}")

        if content:
            c_disp = extract_user_request(content) if stype == "USER_INPUT" else content
            if len(c_disp) > max_chars:
                c_disp = c_disp[:max_chars] + f"\n... [truncated {len(c_disp) - max_chars} chars]"
            print(f"[CONTENT]:\n{c_disp}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan and inspect Antigravity trajectories for skill evaluation.")
    parser.add_argument("--brain-dir", default=str(DEFAULT_BRAIN_DIR), help="Path to Antigravity brain directory.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    p_disc = subparsers.add_parser("discover", help="Find all trajectories where a skill was used.")
    p_disc.add_argument("--skill", required=True, help="Skill name to search for (e.g., photo-analysis).")
    p_disc.add_argument("--executed-only", action="store_true", help="Only show trajectories where the skill was executed or delegated.")
    p_disc.add_argument("--json", action="store_true", help="Output raw JSON.")

    p_insp = subparsers.add_parser("inspect", help="Inspect steps of a specific trajectory.")
    p_insp.add_argument("--conversation-id", required=True, help="Conversation UUID to inspect.")
    p_insp.add_argument("--summary", action="store_true", help="Print conversation diagnostic overview, anomaly counts, and flagged step numbers.")
    p_insp.add_argument("--steps", default="", help="Comma-separated step indices or ranges (e.g. '0,1,5-10').")
    p_insp.add_argument("--full", action="store_true", help="Read from transcript_full.jsonl instead of transcript.jsonl.")
    p_insp.add_argument("--thinking", action="store_true", default=True, help="Include model thinking traces (default: True).")
    p_insp.add_argument("--planner-only", action="store_true", help="Only show USER_INPUT and PLANNER_RESPONSE steps.")
    p_insp.add_argument("--errors-only", action="store_true", help="Only show steps with errors or non-zero exit codes.")
    p_insp.add_argument("--max-chars", type=int, default=2000, help="Max characters per field to display (default: 2000).")

    args = parser.parse_args()
    if args.subcommand == "discover":
        cmd_discover(args)
    elif args.subcommand == "inspect":
        cmd_inspect(args)


if __name__ == "__main__":
    main()
