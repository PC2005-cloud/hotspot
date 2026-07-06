"""
DeepSeek PoW 求解器 — 直接加载官方 WASM 二进制求解

参考自 deepseek-free-api 项目（pow_solver.js + sha3_wasm_bg.wasm）：
- https://github.com/Fly143/deepseek-free-api
- 原始方案用 Node.js 子进程调 WASM
- 本实现改用 wasmtime Python 包直接加载 WASM，免除 Node.js 依赖
"""

import json
import base64
import os
import struct
import logging
import ctypes

logger = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WASM_PATH = os.path.join(SCRIPT_DIR, "sha3_wasm_bg.wasm")


def solve_pow_challenge(config: dict) -> str | None:
    """
    求解 PoW 挑战，返回 base64 编码的响应（可直接作为 x-ds-pow-response header）

    参考 deepseek-free-api/pow_solver.js + pow_native.py：
    - 直接加载官方 WASM 二进制（sha3_wasm_bg.wasm）
    - 用 wasmtime 替代 Node.js 作为 WASM 运行时
    """
    challenge = config["challenge"]
    salt = config["salt"]
    difficulty = config["difficulty"]
    expire_at = config["expire_at"]
    prefix = f"{salt}_{expire_at}_"

    answer = _wasm_solve(challenge, prefix, difficulty)
    if answer is None:
        logger.error("PoW 求解失败（WASM 返回无解）")
        return None

    logger.info(f"PoW 求解成功: nonce={answer}")

    result = {
        "algorithm": config.get("algorithm", "DeepSeekHashV1"),
        "challenge": challenge,
        "salt": salt,
        "answer": answer,
        "signature": config.get("signature", ""),
        "target_path": config.get("target_path", "/api/v0/chat/completion"),
    }
    return base64.b64encode(json.dumps(result, ensure_ascii=False).encode()).decode()


def _wasm_solve(challenge: str, prefix: str, difficulty: int) -> int | None:
    """
    用 wasmtime 调用官方 WASM wasm_solve 函数

    对照参考项目 pow_solver.js 的实现：
    1. __wbindgen_add_to_stack_pointer(-16) → 分配返回值槽位
    2. __wbindgen_export_0(len, 1) → 分配字符串内存
    3. 用 memory.data_ptr() 直接写入字节（同 JS 的 Uint8Array(mem.buffer)）
    4. 调用 wasm_solve(retptr, ch_ptr, ch_len, pfx_ptr, pfx_len, difficulty)
    5. 从 retptr 读取结果：+0=i32(status), +8=f64(answer)
    6. __wbindgen_add_to_stack_pointer(16) → 恢复栈
    """
    import wasmtime

    if not os.path.isfile(WASM_PATH):
        logger.error(f"WASM 文件不存在: {WASM_PATH}")
        return None

    with open(WASM_PATH, "rb") as f:
        wasm_bytes = f.read()

    engine = wasmtime.Engine()
    module = wasmtime.Module(engine, wasm_bytes)
    store = wasmtime.Store(engine)
    linker = wasmtime.Linker(engine)
    instance = linker.instantiate(store, module)

    memory = instance.exports(store)["memory"]
    wasm_solve = instance.exports(store)["wasm_solve"]
    alloc = instance.exports(store)["__wbindgen_export_0"]
    stack_alloc = instance.exports(store)["__wbindgen_add_to_stack_pointer"]

    # 获取 WASM 线性内存的 ctypes 指针（等价于 JS 的 new Uint8Array(mem.buffer)）
    mem_ptr = memory.data_ptr(store)
    mem_buf = ctypes.cast(mem_ptr, ctypes.POINTER(ctypes.c_uint8))

    def _write_str(s: str) -> tuple:
        """写入字符串到 WASM 内存，返回 (ptr, len)"""
        encoded = s.encode("utf-8")
        ptr = alloc(store, len(encoded), 1)
        for i, b in enumerate(encoded):
            mem_buf[ptr + i] = b
        return ptr, len(encoded)

    # 1. 分配返回值空间
    retptr = stack_alloc(store, -16)

    # 2. 写入参数字符串
    ch_ptr, ch_len = _write_str(challenge)
    pfx_ptr, pfx_len = _write_str(prefix)

    # 3. 调用 wasm_solve
    wasm_solve(store, retptr, ch_ptr, ch_len, pfx_ptr, pfx_len, float(difficulty))

    # 4. 读取结果（同 JS: Int32Array(mem.buffer)[retptr/4] 和 Float64Array[...]）
    def _read_i32(offset: int) -> int:
        return struct.unpack("<i", bytes(mem_buf[offset:offset+4]))[0]

    def _read_f64(offset: int) -> float:
        return struct.unpack("<d", bytes(mem_buf[offset:offset+8]))[0]

    status = _read_i32(retptr)
    answer = _read_f64(retptr + 8)

    # 5. 恢复栈
    stack_alloc(store, 16)

    if status == 0:
        return None
    return int(answer)
