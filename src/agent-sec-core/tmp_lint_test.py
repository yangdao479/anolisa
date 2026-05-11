"""Temporary file to test CI ruff lint warning output.
DELETE THIS FILE after verifying CI behavior.
"""

import os
import subprocess


# ---- ANN001: 函数参数缺少类型标注 ----
def greet(name):
    return f"Hello, {name}"


# ---- ANN201: 公有函数缺少返回类型标注 ----
def add(a: int, b: int):
    return a + b


# ---- B006: 可变默认参数 ----
def process(items=[]):
    items.append("x")
    return items


# ---- S602: subprocess shell=True ----
def run_cmd(cmd: str) -> str:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout


# ---- S605: os.system() ----
def exec_system(cmd: str) -> int:
    return os.system(cmd)


# ---- PLW1510: subprocess.run() 未指定 check ----
def run_no_check(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd)


# ---- S108: 硬编码 /tmp 路径 ----
def get_tmp_file() -> str:
    return "/tmp/test_output.txt"


# ---- PLC0415: 函数体内导入 ----
def lazy_load() -> str:
    import json

    return json.dumps({"test": True})
