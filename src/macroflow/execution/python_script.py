"""Python business logic in a disposable process; input stays in the player."""
from __future__ import annotations

import contextlib
import multiprocessing
import os
from pathlib import Path
import sys
import traceback


class ScriptContext:
    def __init__(self, connection, directory: Path):
        self._connection = connection
        self._directory = directory

    def _call(self, method, **kwargs):
        self._connection.send((method, kwargs))
        ok, result = self._connection.recv()
        if not ok:
            raise RuntimeError(result)
        return result

    def action(self, action: dict):
        return self._call("action", action=action)

    def press(self, key: str | int, hold_ms: int = 30):
        names = {"ENTER": 13, "SPACE": 32, "TAB": 9, "ESC": 27,
                 "LEFT": 37, "UP": 38, "RIGHT": 39, "DOWN": 40,
                 "SHIFT": 16, "CTRL": 17, "ALT": 18, "BACKSPACE": 8, "DELETE": 46}
        if isinstance(key, str):
            key = key.upper()
            if len(key) == 1 and key.isascii() and key.isalnum():
                key = ord(key)
            elif key.startswith("F") and key[1:].isdecimal() and 1 <= int(key[1:]) <= 24:
                key = 111 + int(key[1:])
            else:
                key = names.get(key)
        if isinstance(key, bool) or not isinstance(key, int) or not 1 <= key <= 255:
            raise ValueError("无效按键；使用字母、数字、常用键名或 Windows 虚拟键码")
        return self.action({"type": "key_press", "vk": key, "hold_ms": int(hold_ms)})

    def click(self, x: int, y: int, button: str = "left"):
        return self.action({"type": "click", "x": x, "y": y, "button": button})

    def wait(self, ms: float = 0, seconds: float = 0):
        return self.action({"type": "delay", "ms": int(ms + seconds * 1000)})

    def log(self, message):
        return self._call("log", message=str(message))

    def exists(self, template: str, region=None, threshold: float = 0.85) -> bool:
        return self._call("exists", template=str((self._directory / template).resolve()),
                          region=region, threshold=threshold)

    def read_text(self, region=None) -> str:
        return self._call("read_text", region=region)

    def run_module(self, key: str):
        return self._call("module", key=key)

    def run_script(self, path: str, repeats: int = 1):
        return self.action({"type": "script_ref", "script": str((self._directory / path).resolve()),
                            "repeats": repeats})

    def checkpoint(self):
        return self._call("checkpoint")


class _LogWriter:
    def __init__(self, connection):
        self.connection = connection
        self.buffer = ""

    def write(self, text):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self.connection.send(("print", {"message": line}))
        return len(text)

    def flush(self):
        if self.buffer:
            self.connection.send(("print", {"message": self.buffer}))
            self.buffer = ""


def _worker(path: str, connection, resumed):
    script = Path(path)
    os.chdir(script.parent)
    sys.path.insert(0, str(script.parent))
    directory = str(script.parent) + os.sep

    def trace(frame, event, arg):
        if frame.f_code.co_filename.startswith(directory):
            resumed.wait()
        return trace

    writer = _LogWriter(connection)
    try:
        code = compile(script.read_bytes(), str(script), "exec")
        namespace = {"__name__": "macroflow_user_script", "__file__": str(script)}
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            sys.settrace(trace)
            try:
                exec(code, namespace)
                main = namespace.get("main")
                if not callable(main):
                    raise ValueError("Python 脚本必须定义 main(ctx)")
                main(ScriptContext(connection, script.parent))
            finally:
                sys.settrace(None)
                writer.flush()
        connection.send(("done", {}))
    except BaseException:
        connection.send(("error", {"message": traceback.format_exc()}))
    finally:
        connection.close()


def _request(method, data, player, hwnd, stack, depth, module_bindings):
    if method == "action":
        return player._execute_action(data["action"], hwnd, script_stack=stack, depth=depth + 1)
    if method == "log":
        player._log_event(data["message"])
    elif method == "checkpoint":
        return None
    elif method == "module":
        from macroflow.core.storage import load_module_objects
        from macroflow.core.modules import module_action_for_key
        objects = load_module_objects()
        key = module_bindings.get(data["key"], data["key"])
        if key not in objects:
            keys = [k for k, obj in objects.items() if obj.get("name") == key]
            if len(keys) != 1:
                raise ValueError(f"模块名称不存在或不唯一：{key}，请使用模块 ID")
            key = keys[0]
        return player._execute_action(module_action_for_key(key, objects[key]["category"]),
                                      hwnd, script_stack=stack, depth=depth + 1)
    elif method in ("exists", "read_text"):
        region = data.get("region")
        if region is not None:
            if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
                raise ValueError("识别区域需要 (x, y, w, h)，宽高必须大于零")
            region = player._scale_region(tuple(region))
        if method == "exists":
            from macroflow.core.image_match import find_template
            return find_template(data["template"], float(data["threshold"]), region,
                                 scale=player._template_scale()) is not None
        from macroflow.core.ocr import recognize_region
        if player.on_ocr_engine_wait and not player.on_ocr_engine_wait():
            from .player.control import PlaybackStopped
            raise PlaybackStopped()
        return recognize_region(region)
    else:
        raise ValueError(f"未知 Python 脚本接口：{method}")


def run_python_script(path, player, hwnd, script_stack, depth, module_bindings=None):
    from .player.control import (
        PlaybackStopped, EndCurrentScriptRequest, EndCurrentScriptRepeatRequest,
        AdvanceToNextWorkflowStep, GuardJumpRequest, JumpToCurrentScriptLastAction,
    )
    from .player.base import MAX_SCRIPT_REF_DEPTH
    from macroflow.core.storage import resolve_path
    script = resolve_path(path).resolve()
    if not script.is_file() or script.suffix.lower() != ".py":
        raise ValueError(f"Python 脚本不存在或扩展名不是 .py：{script}")
    stack = set(script_stack or ())
    if str(script) in stack or depth >= MAX_SCRIPT_REF_DEPTH:
        raise RuntimeError(f"Python 脚本循环引用或嵌套过深：{script}")
    stack.add(str(script))
    spawn = multiprocessing.get_context("spawn")
    parent, child = spawn.Pipe()
    resumed = spawn.Event()
    resumed.set()
    process = spawn.Process(target=_worker, args=(str(script), child, resumed), daemon=True)
    try:
        process.start()
        child.close()
        while True:
            if player.stop_event.is_set():
                raise PlaybackStopped()
            if player.paused:
                resumed.clear()
            player.wait_while_paused()
            resumed.clear()
            player._poll_guards()
            player._settle_after_guard()
            resumed.set()
            if not parent.poll(0.02):
                if not process.is_alive() and not parent.poll():
                    raise RuntimeError(f"Python 脚本进程异常退出（{process.exitcode}）：{script}")
                continue
            try:
                method, data = parent.recv()
            except EOFError as exc:
                raise RuntimeError(f"Python 脚本连接中断：{script}") from exc
            if method == "done":
                return None
            if method == "error":
                raise RuntimeError(data["message"])
            if method == "print":
                player._log_event(data["message"])
                continue
            try:
                result = _request(method, data, player, hwnd, stack, depth, module_bindings or {})
            except (PlaybackStopped, EndCurrentScriptRequest, EndCurrentScriptRepeatRequest,
                    AdvanceToNextWorkflowStep, GuardJumpRequest, JumpToCurrentScriptLastAction):
                raise
            except Exception as exc:
                parent.send((False, str(exc)))
                continue
            if isinstance(result, tuple) and method in ("action", "module"):
                return result
            parent.send((True, result))
    finally:
        resumed.set()
        parent.close()
        child.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            process.close()
