#!/usr/bin/env python3
# ==============================================================================
# task_manager.py — Pyrmethus AIChat Task Management Tool v1.4.0-ENTERPRISE
# argc/aichat compatible · Human-Readable Colorized Outputs · Active Timer Monitoring
#
# @describe A comprehensive task manager with background LLM wakeup timers and active timer process monitoring.
#
# @option --action! <ACTION>             Action: add, list, complete, delete, clear, schedule, timers, cancel-timer (required)
# @option --title <TITLE>                Title of the task (required for add)
# @option --desc <DESC>                  Optional description of the task
# @option --priority <PRIORITY>          Priority: high, medium, low (default: medium)
# @option --task-id <ID>                 Task ID (required for complete, delete, cancel-timer)
# @option --file <PATH>                  JSON file to store tasks (default: tasks.json)
# @option --wakeup <MINUTES>             Set a background wakeup timer in minutes for the task
# @option --schedule <EXPR>              Schedule task at absolute time ("2026-12-31 14:30") or relative duration ("2h30m")
# @option --agent-prompt <PROMPT>        Agent prompt to auto-execute via aichat upon wakeup
# @option --recurring <MINUTES>          Make the wakeup timer recurring every N minutes
# @option --depends-on <ID>              Task ID this task depends on
# @option --webhook <URL>                Webhook URL to POST when agent finishes
# @option --retry-count <N>              Number of times to retry agent prompt on failure
# @option --timeout <SEC>                Timeout in seconds for agent execution
# @flag   --auto-subtasks                Parse agent output to auto-create subtasks
# @option --context-file <PATH>          File path to include as context for the agent
# @option --assignee <NAME>              CLI tool/agent to use (default: aichat)
# @flag   --auto-resolve                 Automatically mark task completed when agent succeeds
# @option --condition-cmd <CMD>          Only trigger agent if this shell command exits with 0
# @option --max-runs <N>                 Terminate recurring task after N successful runs
# @option --jitter <SEC>                 Add random jitter to the wakeup timer
# @option --escalate-on-fail <AGENT>     Fallback agent if all retries fail
# @flag   --require-approval             Pause and wait for user approval before agent runs
# @flag   --notify-on-start              Send a notification when the agent begins execution
# @option --output-file <PATH>           Save agent stdout to a specific file
# @flag   --silent-success               Only notify LLM_OUTPUT if the agent fails
# @option --env-vars <JSON>              Custom environment variables for agent
# @option --tags <LIST>                  Comma-separated tags (e.g., trading,urgent)
# @option --priority-boost <MINS>        Boost task to HIGH priority after N minutes
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import enum
import json
import logging
import os
import pathlib
import re
import signal
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

__version__ = "1.4.0-ENTERPRISE"
__all__ = ["run", "execute_tool", "ToolJSONEncoder", "__version__"]

# ==============================================================================
# SECTION 1: Exit Codes & JSON Encoder
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_INVALID_INPUT = 2


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Path, Enum, datetime, timedelta, bytes, and sets safely."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, enum.Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return obj.total_seconds()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        return super().default(obj)


# ==============================================================================
# SECTION 2: Color Palette & Formatting Helpers
# ==============================================================================

NEON_CYAN    = "\033[38;5;51m"
NEON_GREEN   = "\033[38;5;46m"
NEON_RED     = "\033[38;5;196m"
NEON_YELLOW  = "\033[38;5;226m"
NEON_PURPLE  = "\033[38;5;129m"
NEON_PINK    = "\033[38;5;198m"
RESET        = "\033[0m"
BOLD         = "\033[1m"
DIM          = "\033[2m"

_ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]"
)

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)

def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")

def _cprint(text: str, file: Any = None, no_color: bool = False) -> None:
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True)

def print_ui(data: dict[str, Any], no_color: bool = False) -> None:
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    
    border = "─" * 68

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [TASK MANAGER v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {data.get('message', 'Complete')}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)

    action = data.get("action")
    if action in {"list", "schedule", "timers"} and success:
        tasks = data.get("tasks", [])
        if not tasks:
            _cprint(f"{NEON_PURPLE}│{RESET} {DIM}No tasks found.{RESET}", no_color=no_color)
        else:
            labels = {"schedule": "Scheduled Tasks", "timers": "Active LLM Wakeup Timers", "list": f"Current Tasks ({len(tasks)})"}
            _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}{labels.get(action, 'Tasks')}:{RESET}", no_color=no_color)
            for t in tasks:
                status_icon = "☑" if t.get("completed") else "☐"
                pri = t.get("priority", "medium")
                pri_color = NEON_RED if pri == "high" else (NEON_YELLOW if pri == "medium" else NEON_GREEN)
                timer_info = f" [⏱️ PID: {t.get('timer_pid')}]" if t.get("timer_active") else ""
                sched_info = f" (Sched: {t.get('schedule_expr')})" if t.get("schedule_expr") else ""
                _cprint(f"{NEON_PURPLE}│{RESET} {status_icon} [{t['id'][:6]}] {pri_color}{pri.upper()}{RESET} - {t['title']}{sched_info}{timer_info}", no_color=no_color)
                if t.get("desc"):
                    _cprint(f"{NEON_PURPLE}│{RESET}     {DIM}{t['desc']}{RESET}", no_color=no_color)
    else:
        if "task" in data and data["task"]:
            t = data["task"]
            _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Task ID:{RESET} {t['id']}", no_color=no_color)
            _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Title:{RESET}   {t['title']}", no_color=no_color)

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}   {data['error']}", no_color=no_color)

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}", no_color=no_color)

# ==============================================================================
# SECTION 3: Schedule Parser & Core Logic
# ==============================================================================

def parse_schedule(schedule_str: str) -> int:
    """Parse advanced schedule strings (absolute datetime or complex relative durations like 2h30m, 1d) into minutes."""
    if not schedule_str:
        return 0
    schedule_str = schedule_str.strip().lower()
    
    # Check compound relative duration (e.g. 2h30m, 1d12h)
    total_mins = 0
    matches = re.findall(r"(\d+)([smhd])", schedule_str)
    if matches:
        for val_str, unit in matches:
            val = int(val_str)
            if unit == 's': total_mins += max(1, val // 60)
            elif unit == 'm': total_mins += val
            elif unit == 'h': total_mins += val * 60
            elif unit == 'd': total_mins += val * 1440
        return total_mins

    # Try absolute datetime parsing
    try:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(schedule_str, fmt)
                delta_mins = (dt - datetime.now()).total_seconds() / 60.0
                return max(0, int(delta_mins))
            except ValueError:
                continue
        dt = datetime.fromisoformat(schedule_str)
        delta_mins = (dt - datetime.now()).total_seconds() / 60.0
        return max(0, int(delta_mins))
    except Exception:
        pass
    return 0

def is_pid_running(pid: Optional[int]) -> bool:
    """Check if a background timer process PID is currently active."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True

def resolve_task_by_prefix(tasks: list[dict[str, Any]], prefix: str) -> Optional[dict[str, Any]]:
    """Safely resolve a task by ID prefix, returning the task or raising ValueError if ambiguous."""
    if not prefix:
        return None
    matches = [t for t in tasks if t["id"].startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        raise ValueError(f"Ambiguous task ID prefix '{prefix}' matches multiple tasks.")
    return None

def load_tasks(file_path: Path) -> list[dict[str, Any]]:
    if not file_path.exists():
        return []
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        # Housekeeping: auto-clear dead PIDs
        updated = False
        for t in data:
            if t.get("timer_pid") and not is_pid_running(t["timer_pid"]):
                t["timer_pid"] = None
                updated = True
        if updated:
            save_tasks(file_path, data)
        return data
    except Exception:
        return []

def save_tasks(file_path: Path, tasks: list[dict[str, Any]]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_name(f".{file_path.name}.tmp_{os.getpid()}")
    try:
        temp_path.write_text(json.dumps(tasks, indent=2, cls=ToolJSONEncoder), encoding="utf-8")
        temp_path.replace(file_path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

class GracefulShutdown:
    """Signal handler for graceful cancellation."""

    def __init__(self) -> None:
        self.interrupted = False
        try:
            self._old_sigint = signal.signal(signal.SIGINT, self._handler)
            self._old_sigterm = signal.signal(signal.SIGTERM, self._handler)
        except ValueError:
            self._old_sigint = signal.SIG_DFL
            self._old_sigterm = signal.SIG_DFL

    def _handler(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        try:
            signal.signal(signal.SIGINT, self._old_sigint)
            signal.signal(signal.SIGTERM, self._old_sigterm)
        except ValueError:
            pass


def execute_tool(
    action: str,
    title: str = "",
    desc: str = "",
    priority: str = "medium",
    task_id: str = "",
    file: str = "tasks.json",
    wakeup: int = 0,
    schedule: str = "",
    agent_prompt: str = "",
    recurring: int = 0,
    depends_on: str = "",
    webhook: str = "",
    retry_count: int = 0,
    timeout: int = 0,
    auto_subtasks: bool = False,
    context_file: str = "",
    assignee: str = "aichat",
    auto_resolve: bool = False,
    condition_cmd: str = "",
    max_runs: int = 0,
    jitter: int = 0,
    escalate_on_fail: str = "",
    require_approval: bool = False,
    notify_on_start: bool = False,
    output_file: str = "",
    silent_success: bool = False,
    env_vars: str = "",
    tags: str = "",
    priority_boost: int = 0,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Executing task action: {action}")

    file_path = Path(file).expanduser().resolve()
    tasks = load_tasks(file_path)

    effective_wakeup = wakeup
    if schedule:
        parsed_mins = parse_schedule(schedule)
        if parsed_mins > 0:
            effective_wakeup = parsed_mins

    shutdown = GracefulShutdown()

    try:
        if action == "add":
            if not title:
                return {"success": False, "action": action, "error": "--title is required for add.", "exit_code": EXIT_INVALID_INPUT}
            
            new_task = {
                "id": str(uuid.uuid4()),
                "title": title,
                "desc": desc,
                "priority": priority.lower(),
                "completed": False,
                "created_at": datetime.now().isoformat(),
                "wakeup_mins": effective_wakeup if effective_wakeup > 0 else None,
                "schedule_expr": schedule if schedule else None,
                "agent_prompt": agent_prompt if agent_prompt else None,
                "recurring_mins": recurring if recurring > 0 else None,
                "depends_on": depends_on if depends_on else None,
                "webhook": webhook if webhook else None,
                "retry_count": retry_count,
                "timeout": timeout if timeout > 0 else None,
                "auto_subtasks": auto_subtasks,
                "context_file": context_file if context_file else None,
                "assignee": assignee,
                "auto_resolve": auto_resolve,
                "condition_cmd": condition_cmd if condition_cmd else None,
                "max_runs": max_runs if max_runs > 0 else None,
                "jitter": jitter if jitter > 0 else None,
                "escalate_on_fail": escalate_on_fail if escalate_on_fail else None,
                "require_approval": require_approval,
                "notify_on_start": notify_on_start,
                "output_file": output_file if output_file else None,
                "silent_success": silent_success,
                "env_vars": env_vars if env_vars else None,
                "tags": [t.strip() for t in tags.split(",")] if tags else [],
                "priority_boost_mins": priority_boost if priority_boost > 0 else None,
                "subtasks": [],
                "timer_pid": None
            }
            
            msg = "Task added successfully."
            if effective_wakeup > 0:
                import subprocess
                wakeup_seconds = effective_wakeup * 60
                sleeper_script = f"""
import time, json, os, sys, subprocess, urllib.request, random, re
from datetime import datetime

task_id = "{new_task['id']}"
task_title = {repr(new_task['title'])}
recurring = {recurring}
depends_on = {repr(depends_on)}
webhook = {repr(webhook)}
retry_count = {retry_count}
timeout_sec = {timeout if timeout > 0 else 300}
context_file = {repr(context_file)}
assignee = {repr(assignee)}
auto_resolve = {auto_resolve}
condition_cmd = {repr(condition_cmd)}
max_runs = {max_runs}
jitter = {jitter}
escalate_on_fail = {repr(escalate_on_fail)}
require_approval = {require_approval}
notify_on_start = {notify_on_start}
output_file = {repr(output_file)}
silent_success = {silent_success}
env_vars_str = {repr(env_vars)}
auto_subtasks = {auto_subtasks}
priority_boost_mins = {priority_boost if priority_boost > 0 else 0}
created_time = datetime.now()

def write_llm(data):
    out_path = "{os.environ.get('LLM_OUTPUT', '/dev/stdout')}"
    if out_path not in {{"/dev/stdout", "/dev/fd/1", "-"}}:
        try:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(data) + "\\n")
        except:
            pass

def run_agent(current_assignee):
    if depends_on:
        try:
            with open("{file_path}", "r") as f:
                tsks = json.load(f)
                dep = next((t for t in tsks if t["id"].startswith(depends_on)), None)
                if dep and not dep["completed"]:
                    return False, "Dependency not met"
        except:
            pass

    if condition_cmd and subprocess.call(condition_cmd, shell=True) != 0:
        return False, "Condition command failed"

    if require_approval:
        write_llm({{"success": True, "action": "approval_needed", "message": f"✋ Task {{task_title}} requires approval.", "task_id": task_id}})
        time.sleep(30)

    if notify_on_start:
        write_llm({{"success": True, "action": "notify", "message": f"🚀 Task {{task_title}} starting execution...", "task_id": task_id}})

    script_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
    cmd = current_assignee
    if cmd in ("bbt", "bbt.py"):
        cmd = os.path.join(script_dir, "bbt.py")
        
    if cmd.endswith(".py") or os.path.exists(os.path.join(script_dir, cmd + ".py")):
        if not cmd.endswith(".py"):
            cmd = os.path.join(script_dir, cmd + ".py")
        prompt_args = [sys.executable, cmd]
    else:
        prompt_args = [cmd]

    # If the assignee is a python script, we don't want to pass the raw prompt 
    # as a single string if it contains CLI arguments. We split it if it looks like CLI arguments.
    # Otherwise we pass it as a single argument.
    prompt_str = {repr(agent_prompt)}
    if cmd.endswith(".py") and prompt_str.startswith("-"):
        import shlex
        prompt_args.extend(shlex.split(prompt_str))
    else:
        if context_file and os.path.exists(context_file):
            with open(context_file, "r") as f:
                prompt_args.append(f.read() + "\\n\\n" + prompt_str)
        else:
            prompt_args.append(prompt_str)
        
    env = os.environ.copy()
    if env_vars_str:
        try:
            env.update(json.loads(env_vars_str))
        except:
            pass

    success = False
    response = ""
    for attempt in range(retry_count + 1):
        try:
            res = subprocess.check_output(prompt_args, text=True, timeout=timeout_sec, env=env)
            response = res.strip()
            success = True
            break
        except Exception as e:
            response = f"Attempt {{attempt+1}} Failed: {{e}}"
            time.sleep(2)
            
    if output_file and success:
        try:
            with open(output_file, "w") as f:
                f.write(response)
        except:
            pass

    if success and auto_subtasks and response:
        subtasks = re.findall(r"^\\s*-\\s*\\[[ \\]]\\s*(.+)$", response, re.MULTILINE)
        if subtasks:
            try:
                with open("{file_path}", "r") as f:
                    tsks = json.load(f)
                for t in tsks:
                    if t["id"] == task_id:
                        t.setdefault("subtasks", []).extend(subtasks)
                with open("{file_path}", "w") as f:
                    json.dump(tsks, f, indent=2)
            except:
                pass
            
    return success, response

runs = 0
while True:
    wait_time = {wakeup_seconds}
    if jitter > 0:
        wait_time += random.randint(-jitter, jitter)
    time.sleep(max(1, wait_time))

    if priority_boost_mins > 0:
        try:
            elapsed_mins = (datetime.now() - created_time).total_seconds() / 60
            if elapsed_mins >= priority_boost_mins:
                with open("{file_path}", "r") as f:
                    tsks = json.load(f)
                for t in tsks:
                    if t["id"] == task_id and t.get("priority") != "high":
                        t["priority"] = "high"
                with open("{file_path}", "w") as f:
                    json.dump(tsks, f, indent=2)
        except:
            pass
    
    agent_response = None
    agent_success = False
    if {repr(agent_prompt)}:
        agent_success, agent_response = run_agent(assignee)
        if not agent_success and escalate_on_fail:
            write_llm({{"success": False, "action": "escalate", "message": f"⚠️ Escalating to {{escalate_on_fail}}", "task_id": task_id}})
            agent_success, agent_response = run_agent(escalate_on_fail)

    if silent_success and agent_success:
        msg = None
    else:
        msg = f"⏰ LLM Wakeup Triggered for Task: {{task_title}}"
        if agent_response:
            msg += "\\n\\n🤖 Agent Output:\\n" + agent_response

    if agent_success and auto_resolve:
        try:
            with open("{file_path}", "r") as f:
                tsks = json.load(f)
            for t in tsks:
                if t["id"] == task_id:
                    t["completed"] = True
            with open("{file_path}", "w") as f:
                json.dump(tsks, f, indent=2)
        except:
            pass
            
    if webhook and agent_success:
        try:
            req = urllib.request.Request(webhook, data=json.dumps({{"task": task_title, "response": agent_response}}).encode(), headers={{'Content-Type': 'application/json'}})
            urllib.request.urlopen(req, timeout=5)
        except:
            pass

    if msg:
        write_llm({{"success": True, "action": "wakeup", "message": msg, "task_id": task_id}})
            
    runs += 1
    if not recurring or (max_runs > 0 and runs >= max_runs):
        try:
            with open("{file_path}", "r") as f:
                tsks = json.load(f)
            for t in tsks:
                if t["id"] == task_id:
                    t["timer_pid"] = None
            with open("{file_path}", "w") as f:
                json.dump(tsks, f, indent=2)
        except:
            pass
        break
"""
                proc = subprocess.Popen([sys.executable, "-c", sleeper_script], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                new_task["timer_pid"] = proc.pid
                msg += f" Wakeup timer process spawned (PID: {proc.pid})."

            tasks.append(new_task)
            save_tasks(file_path, tasks)
            return {"success": True, "action": action, "message": msg, "task": new_task, "exit_code": EXIT_SUCCESS}

        elif action == "list":
            pri_map = {"high": 0, "medium": 1, "low": 2}
            for t in tasks:
                t["timer_active"] = is_pid_running(t.get("timer_pid"))
            sorted_tasks = sorted(
                tasks,
                key=lambda t: (t.get("completed", False), pri_map.get(t.get("priority", "medium"), 1), t.get("created_at", ""))
            )
            return {"success": True, "action": action, "message": "Tasks retrieved.", "tasks": sorted_tasks, "exit_code": EXIT_SUCCESS}

        elif action == "schedule":
            scheduled_tasks = []
            for t in tasks:
                if t.get("wakeup_mins") or t.get("schedule_expr"):
                    t["timer_active"] = is_pid_running(t.get("timer_pid"))
                    scheduled_tasks.append(t)
            return {"success": True, "action": action, "message": "Scheduled tasks retrieved.", "tasks": scheduled_tasks, "exit_code": EXIT_SUCCESS}

        elif action == "timers":
            active_timers = []
            for t in tasks:
                pid = t.get("timer_pid")
                if is_pid_running(pid):
                    t["timer_active"] = True
                    active_timers.append(t)
                else:
                    t["timer_pid"] = None
                    t["timer_active"] = False
            save_tasks(file_path, tasks)
            return {"success": True, "action": action, "message": "Active wakeup timers retrieved.", "tasks": active_timers, "exit_code": EXIT_SUCCESS}

        elif action == "cancel-timer":
            if not task_id:
                return {"success": False, "action": action, "error": "--task-id is required to cancel a timer.", "exit_code": EXIT_INVALID_INPUT}
            
            try:
                target = resolve_task_by_prefix(tasks, task_id)
            except ValueError as err:
                return {"success": False, "action": action, "error": str(err), "exit_code": EXIT_INVALID_INPUT}

            if not target:
                return {"success": False, "action": action, "error": f"Task {task_id} not found.", "exit_code": EXIT_ERROR}

            pid = target.get("timer_pid")
            if is_pid_running(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            target["timer_pid"] = None
            target["wakeup_mins"] = None
            target["schedule_expr"] = None
            save_tasks(file_path, tasks)
            return {"success": True, "action": action, "message": f"Wakeup timer cancelled for task {target['title']}.", "task": target, "exit_code": EXIT_SUCCESS}

        elif action == "complete":
            if not task_id:
                return {"success": False, "action": action, "error": "--task-id is required to complete.", "exit_code": EXIT_INVALID_INPUT}
            
            try:
                target = resolve_task_by_prefix(tasks, task_id)
            except ValueError as err:
                return {"success": False, "action": action, "error": str(err), "exit_code": EXIT_INVALID_INPUT}

            if not target:
                return {"success": False, "action": action, "error": f"Task {task_id} not found.", "exit_code": EXIT_ERROR}

            target["completed"] = True
            pid = target.get("timer_pid")
            if is_pid_running(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            target["timer_pid"] = None
            save_tasks(file_path, tasks)
            return {"success": True, "action": action, "message": "Task marked as completed.", "task": target, "exit_code": EXIT_SUCCESS}

        elif action == "delete":
            if not task_id:
                return {"success": False, "action": action, "error": "--task-id is required to delete.", "exit_code": EXIT_INVALID_INPUT}
            
            try:
                target = resolve_task_by_prefix(tasks, task_id)
            except ValueError as err:
                return {"success": False, "action": action, "error": str(err), "exit_code": EXIT_INVALID_INPUT}

            if not target:
                return {"success": False, "action": action, "error": f"Task {task_id} not found.", "exit_code": EXIT_ERROR}
            
            pid = target.get("timer_pid")
            if is_pid_running(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass

            filtered = [t for t in tasks if t["id"] != target["id"]]
            save_tasks(file_path, filtered)
            return {"success": True, "action": action, "message": "Task deleted successfully.", "exit_code": EXIT_SUCCESS}
            
        elif action == "clear":
            for t in tasks:
                pid = t.get("timer_pid")
                if is_pid_running(pid):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except OSError:
                        pass
            save_tasks(file_path, [])
            return {"success": True, "action": action, "message": "All tasks and active timers cleared.", "exit_code": EXIT_SUCCESS}

        else:
            return {"success": False, "action": action, "error": f"Unknown action: {action}", "exit_code": EXIT_INVALID_INPUT}

    except Exception as exc:
        return {"success": False, "action": action, "error": f"Execution failure: {exc}", "exit_code": EXIT_ERROR}
    finally:
        shutdown.restore()

def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"

    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError:
            sys.stdout.write(json_payload)
            sys.stdout.flush()

def run(
    action: str,
    title: str = "",
    desc: str = "",
    priority: str = "medium",
    task_id: str = "",
    file: str = "tasks.json",
    wakeup: int = 0,
    schedule: str = "",
    agent_prompt: str = "",
    recurring: int = 0,
    depends_on: str = "",
    webhook: str = "",
    retry_count: int = 0,
    timeout: int = 0,
    auto_subtasks: bool = False,
    context_file: str = "",
    assignee: str = "aichat",
    auto_resolve: bool = False,
    condition_cmd: str = "",
    max_runs: int = 0,
    jitter: int = 0,
    escalate_on_fail: str = "",
    require_approval: bool = False,
    notify_on_start: bool = False,
    output_file: str = "",
    silent_success: bool = False,
    env_vars: str = "",
    tags: str = "",
    priority_boost: int = 0,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    result = execute_tool(
        action=action,
        title=title,
        desc=desc,
        priority=priority,
        task_id=task_id,
        file=file,
        wakeup=wakeup,
        schedule=schedule,
        agent_prompt=agent_prompt,
        recurring=recurring,
        depends_on=depends_on,
        webhook=webhook,
        retry_count=retry_count,
        timeout=timeout,
        auto_subtasks=auto_subtasks,
        context_file=context_file,
        assignee=assignee,
        auto_resolve=auto_resolve,
        condition_cmd=condition_cmd,
        max_runs=max_runs,
        jitter=jitter,
        escalate_on_fail=escalate_on_fail,
        require_approval=require_approval,
        notify_on_start=notify_on_start,
        output_file=output_file,
        silent_success=silent_success,
        env_vars=env_vars,
        tags=tags,
        priority_boost=priority_boost,
        no_color=no_color,
        verbose=verbose,
    )
    print_ui(result, no_color=no_color)
    write_llm_output(result)

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AIChat Task Management Tool")
    parser.add_argument("--action", required=True, choices=["add", "list", "complete", "delete", "clear", "schedule", "timers", "cancel-timer"])
    parser.add_argument("--title", default="")
    parser.add_argument("--desc", default="")
    parser.add_argument("--priority", choices=["high", "medium", "low"], default="medium")
    parser.add_argument("--task-id", dest="task_id", default="")
    parser.add_argument("--file", default="tasks.json")
    parser.add_argument("--wakeup", type=int, default=0, help="Wakeup timer in minutes")
    parser.add_argument("--schedule", default="", help="Schedule task at absolute time or relative duration")
    parser.add_argument("--agent-prompt", dest="agent_prompt", default="", help="Agent prompt to auto-execute via aichat upon wakeup")
    parser.add_argument("--recurring", type=int, default=0, help="Make the wakeup timer recurring every N minutes")
    parser.add_argument("--depends-on", dest="depends_on", default="", help="Task ID this task depends on")
    parser.add_argument("--webhook", default="", help="Webhook URL to POST when agent finishes")
    parser.add_argument("--retry-count", dest="retry_count", type=int, default=0, help="Number of times to retry agent prompt on failure")
    parser.add_argument("--timeout", type=int, default=0, help="Timeout in seconds for agent execution")
    parser.add_argument("--auto-subtasks", dest="auto_subtasks", action="store_true", default=False, help="Parse agent output to auto-create subtasks")
    parser.add_argument("--context-file", dest="context_file", default="", help="File path to include as context for the agent")
    parser.add_argument("--assignee", default="aichat", help="CLI tool/agent to use (default: aichat)")
    parser.add_argument("--auto-resolve", dest="auto_resolve", action="store_true", default=False, help="Automatically mark task completed when agent succeeds")
    parser.add_argument("--condition-cmd", dest="condition_cmd", default="", help="Only trigger agent if this shell command exits with 0")
    parser.add_argument("--max-runs", dest="max_runs", type=int, default=0, help="Terminate recurring task after N successful runs")
    parser.add_argument("--jitter", type=int, default=0, help="Add random jitter to the wakeup timer")
    parser.add_argument("--escalate-on-fail", dest="escalate_on_fail", default="", help="Fallback agent if all retries fail")
    parser.add_argument("--require-approval", dest="require_approval", action="store_true", default=False, help="Pause and wait for user approval before agent runs")
    parser.add_argument("--notify-on-start", dest="notify_on_start", action="store_true", default=False, help="Send a notification when the agent begins execution")
    parser.add_argument("--output-file", dest="output_file", default="", help="Save agent stdout to a specific file")
    parser.add_argument("--silent-success", dest="silent_success", action="store_true", default=False, help="Only notify LLM_OUTPUT if the agent fails")
    parser.add_argument("--env-vars", dest="env_vars", default="", help="Custom environment variables for agent (JSON)")
    parser.add_argument("--tags", default="", help="Comma-separated tags (e.g., trading,urgent)")
    parser.add_argument("--priority-boost", dest="priority_boost", type=int, default=0, help="Boost task to HIGH priority after N minutes")
    parser.add_argument("--no-color", action="store_true", default=False)
    parser.add_argument("--verbose", "-v", action="store_true", default=False, help="Enable detailed debug logging")
    return parser

if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = execute_tool(
        action=args.action,
        title=args.title,
        desc=args.desc,
        priority=args.priority,
        task_id=args.task_id,
        file=args.file,
        wakeup=args.wakeup,
        schedule=args.schedule,
        agent_prompt=args.agent_prompt,
        recurring=args.recurring,
        depends_on=args.depends_on,
        webhook=args.webhook,
        retry_count=args.retry_count,
        timeout=args.timeout,
        auto_subtasks=args.auto_subtasks,
        context_file=args.context_file,
        assignee=args.assignee,
        auto_resolve=args.auto_resolve,
        condition_cmd=args.condition_cmd,
        max_runs=args.max_runs,
        jitter=args.jitter,
        escalate_on_fail=args.escalate_on_fail,
        require_approval=args.require_approval,
        notify_on_start=args.notify_on_start,
        output_file=args.output_file,
        silent_success=args.silent_success,
        env_vars=args.env_vars,
        tags=args.tags,
        priority_boost=args.priority_boost,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    print_ui(res, no_color=no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))