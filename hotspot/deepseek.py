"""
DeepSeek Web API 自动化模块（纯 HTTP 版）

替代原来的 Playwright 方案，直接调用 DeepSeek 网页版内部 API：
- 账号密码登录 → 获取 userToken
- 创建聊天 Session
- PoW 挑战求解（纯 Python SHA3-256）
- SSE 流式接收回复
- Token 过期自动重新登录

依赖：curl-cffi（模拟 Chrome TLS 指纹绕过 Cloudflare）
"""

import os
import re
import json
import time
import random
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# ─── 路径配置 ───────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_DIR = os.path.join(BASE_DIR, "session")
TOKEN_FILE = os.path.join(SESSION_DIR, "token.json")

# ─── 常量 ────────────────────────────────────────────

DS_BASE = "https://chat.deepseek.com"
DS_API = f"{DS_BASE}/api/v0"

# 请求头模板（伪造浏览器）
DS_HEADERS = {
    "content-type": "application/json",
    "origin": DS_BASE,
    "referer": f"{DS_BASE}/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.0.0 Safari/537.36"
    ),
    "x-client-version": "2.0.2",
    "x-client-platform": "web",
    "accept": "*/*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 重试相关
MAX_LOGIN_RETRIES = 3
LOGIN_RETRY_DELAY = 5  # 秒


# ─── 导入 PoW 求解器 ────────────────────────────────

def _import_pow_solver():
    """延迟导入 pow_solver，避免初始化时出错"""
    try:
        from .deepseek_pow import solve_pow_challenge
        return solve_pow_challenge
    except Exception as e:
        logger.error(f"PoW 求解器导入失败: {e}")
        return None


# ─── 浏览器管理器 ────────────────────────────────────

class DeepSeekBot:
    """DeepSeek 网页 API 自动化机器人（纯 HTTP，无浏览器）"""

    def __init__(self, headless: bool = True):
        """
        初始化机器人

        参数：
            headless: 保留兼容，不再使用（纯 HTTP 无需浏览器）
        """
        self.headless = headless
        self._token: Optional[str] = None
        self._session_id: Optional[str] = None
        self._cookie: Optional[str] = None
        self._account: str = ""
        self._password: str = ""
        self._login_type: str = "phone"
        self._area_code: str = "+86"
        self._thinking_enabled: bool = True     # 深度思考，默认开启
        self._search_enabled: bool = True       # 联网搜索，默认开启
        self._session = None  # curl_cffi Session
        self._pow_solver = _import_pow_solver()

    # ─── 启动与登录 ──────────────────────────────

    def start(self):
        """初始化 HTTP 会话（替代原来启动浏览器的逻辑）"""
        logger.info("DeepSeek HTTP 客户端初始化")
        # 尝试加载已保存的 session
        self._load_session()
        if self._token:
            logger.info("已加载保存的 token")
        return self

    def _ensure_session(self):
        """确保有可用的 curl_cffi Session"""
        if self._session is None:
            try:
                from curl_cffi import requests as cffi_requests
                self._session = cffi_requests.Session()
                self._session.impersonate = "chrome120"
            except ImportError:
                logger.error("curl_cffi 未安装，请执行: pip install curl-cffi")
                raise
        return self._session

    def _preflight(self) -> bool:
        """预访问首页，获取 WAF Cookie"""
        try:
            sess = self._ensure_session()
            resp = sess.get(
                DS_BASE + "/",
                headers={"user-agent": DS_HEADERS["user-agent"]},
                timeout=15,
            )
            # 提取 Cookie
            cookies = sess.cookies.get_dict()
            if cookies:
                logger.debug(f"预访问成功, Cookie: {len(cookies)} 个")
            return True
        except Exception as e:
            logger.warning(f"预访问首页失败（不影响登录）: {e}")
            return False

    def login(self, account: str, password: str) -> bool:
        """
        登录 DeepSeek（API 版），返回是否成功

        支持手机号自动识别（+86 区号前缀可省略）
        """
        self._account = account
        self._password = password

        # 判断登录类型：纯数字→手机号，含@→邮箱
        if "@" in account:
            self._login_type = "email"
        elif account.isdigit():
            self._login_type = "phone"
        else:
            self._login_type = "email"

        for attempt in range(1, MAX_LOGIN_RETRIES + 1):
            result = self._do_login()
            if result:
                return True
            if attempt < MAX_LOGIN_RETRIES:
                delay = LOGIN_RETRY_DELAY + random.uniform(1, 3)
                logger.info(f"  登录重试 ({attempt}/{MAX_LOGIN_RETRIES})，等待 {delay:.0f}秒...")
                time.sleep(delay)

        # 尝试用已保存的 token 创建 session（兜底）
        if self._token:
            logger.info("尝试用已保存的 token 创建 session...")
            sess_id = self._create_session()
            if sess_id:
                self._session_id = sess_id
                self._save_session()
                logger.info("✅ 使用已保存 token 恢复 session 成功")
                return True

        return False

    def _do_login(self) -> bool:
        """执行一次登录请求"""
        try:
            sess = self._ensure_session()
            # 预访问获取 WAF Cookie
            self._preflight()

            # 构造登录 payload
            device_id = uuid.uuid4().hex[:16]
            login_payload = {
                "password": self._password,
                "device_id": device_id,
                "os": "web",
            }

            if self._login_type == "email":
                login_payload["email"] = self._account
                login_payload["mobile"] = ""
                login_payload["area_code"] = ""
            else:
                # 手机号
                mobile = self._account
                area_code = self._area_code
                # 如果手机号以 +86 开头，提取区号和号码
                if mobile.startswith("+86"):
                    area_code = "+86"
                    mobile = mobile[3:]
                elif mobile.startswith("+") and mobile[1:].isdigit():
                    # 其他区号
                    parts = mobile.split(" ", 1)
                    if len(parts) == 2:
                        area_code, mobile = parts
                    else:
                        area_code = mobile[:3]
                        mobile = mobile[3:]
                # 纯数字手机号，补区号
                login_payload["mobile"] = mobile
                login_payload["area_code"] = area_code
                login_payload["email"] = ""

            logger.info(f"登录账号: {self._account[:4]}**** (方式: {self._login_type})")

            resp = sess.post(
                f"{DS_API}/users/login",
                json=login_payload,
                headers=DS_HEADERS,
                timeout=30,
            )

            # WAF 拦截检测
            if resp.status_code == 202 and resp.headers.get("x-amzn-waf-action"):
                logger.error("❌ 登录被 AWS WAF 拦截 (HTTP 202)")
                return False

            raw = (resp.text or "").strip()
            if not raw:
                logger.error("❌ 登录失败: 服务器返回空响应")
                return False

            try:
                data = resp.json()
            except Exception:
                logger.error(f"❌ 登录失败: 非 JSON 响应: {raw[:200]}")
                return False

            outer_code = data.get("code", 0)
            # DeepSeek 的响应结构：
            # {code:0, data:{biz_code:N, biz_msg:"...", biz_data:{...}}}
            inner_data = data.get("data") or {}
            biz_code = inner_data.get("biz_code", 0)
            biz_msg = inner_data.get("biz_msg", "")
            biz_data = inner_data.get("biz_data") or {}

            if resp.status_code != 200 or outer_code != 0 or biz_code != 0:
                err_msg = (
                    biz_msg
                    or data.get("msg")
                    or f"HTTP {resp.status_code}/code={outer_code}"
                )
                logger.error(f"❌ 登录失败: {err_msg}")
                return False

            token = biz_data.get("user", {}).get("token", "")
            if not token:
                logger.error("❌ 登录失败: 响应中无 token")
                return False

            self._token = token
            logger.info(f"Token 获取成功: {token[:16]}...{token[-8:]}")

            # 创建聊天 session
            sess_id = self._create_session()
            if not sess_id:
                logger.error("❌ 创建聊天 session 失败")
                return False

            self._session_id = sess_id
            self._save_session()
            logger.info("✅ 登录成功")
            return True

        except Exception as e:
            logger.error(f"❌ 登录异常: {e}")
            return False

    def _create_session(self) -> Optional[str]:
        """创建新的聊天 session，返回 session_id"""
        try:
            sess = self._ensure_session()
            auth_headers = {
                **DS_HEADERS,
                "authorization": f"Bearer {self._token}",
                "referer": DS_BASE + "/",
            }

            resp = sess.post(
                f"{DS_API}/chat_session/create",
                json={},
                headers=auth_headers,
                impersonate="chrome120",
                timeout=15,
            )

            if resp.status_code != 200:
                logger.warning(f"创建 session 失败: HTTP {resp.status_code}")
                return None

            body = resp.json()
            biz = (body.get("data") or {}).get("biz_data") or {}
            session_id = (
                biz.get("chat_session", {}).get("id")
                or biz.get("id")
                or ""
            )
            if session_id:
                logger.debug(f"新 session: {session_id}")
                return session_id

            logger.warning(f"创建 session 失败: 响应中无 ID: {str(body)[:200]}")
            return None

        except Exception as e:
            logger.warning(f"创建 session 异常: {e}")
            return None

    # ─── 深度思考/联网搜索控制 ──────────────────
    # 参考 deepseek-free-api/proxy.py:3179 中 thinking_enabled/search_enabled 的用法
    # 他们通过模型 ID 映射决定 flags，我们通过方法调用来设置

    def enable_all(self):
        """开启深度思考和联网搜索"""
        self._thinking_enabled = True
        self._search_enabled = True
        logger.info("✅ 深度思考 + 联网搜索 已开启")

    def disable_all(self):
        """关闭深度思考和联网搜索"""
        self._thinking_enabled = False
        self._search_enabled = False
        logger.info("深度思考 + 联网搜索 已关闭")

    def set_toggle(self, name: str, target_on: bool = True):
        """设置开关状态"""
        if "深度" in name or "思考" in name:
            self._thinking_enabled = target_on
            logger.info(f"[{'✅' if target_on else '❌'}深度思考] {'开启' if target_on else '关闭'}")
        if "搜索" in name or "联网" in name:
            self._search_enabled = target_on
            logger.info(f"[{'✅' if target_on else '❌'}联网搜索] {'开启' if target_on else '关闭'}")

    # ─── 对话 ──────────────────────────────────

    def new_chat(self):
        """开启新对话（创建新 session）"""
        logger.info("准备开启新对话...")
        sess_id = self._create_session()
        if sess_id:
            self._session_id = sess_id
            self._save_session()
            logger.info("新对话已就绪")
        else:
            logger.warning("创建新对话失败，继续使用当前 session")

    def delete_chat(self):
        """HTTP 版无需删除对话（无侧边栏）"""
        pass

    def chat(self, message: str, timeout: int = 180) -> dict:
        """
        发送消息并接收回复

        参数：
            message: 要发送的消息
            timeout: 等待回复的最大秒数（SSE 读取超时）

        返回：
            {"text": "完整回复文本", "links": [{"text": "链接文字", "url": "链接地址"}, ...]}
        """
        msg_preview = message[:80].replace("\n", " ")
        logger.info(
            f"发送消息 ({len(message)} 字, 超时 {timeout}秒): {msg_preview}..."
        )

        if not self._token or not self._session_id:
            logger.error("未登录或未创建 session，请先调用 login()")
            return {"text": "", "links": []}

        # 1. 获取并求解 PoW 挑战
        pow_header = self._solve_pow()
        if pow_header is None:
            logger.warning("PoW 求解失败，尝试不带 PoW 发送")
            pow_header = ""

        # 2. 发送对话请求
        result = self._send_chat_request(message, pow_header, timeout)

        return result

    def _solve_pow(self) -> Optional[str]:
        """获取 PoW 挑战并求解，返回 x-ds-pow-response header 值"""
        try:
            sess = self._ensure_session()
            auth_headers = {
                **DS_HEADERS,
                "authorization": f"Bearer {self._token}",
                "referer": f"{DS_BASE}/a/chat/s/{self._session_id}",
            }

            resp = sess.post(
                f"{DS_API}/chat/create_pow_challenge",
                headers=auth_headers,
                json={"target_path": "/api/v0/chat/completion"},
                impersonate="chrome120",
                timeout=15,
            )

            if resp.status_code != 200:
                logger.warning(f"PoW 挑战请求失败: HTTP {resp.status_code}")
                return None

            body = resp.json()
            challenge = (
                body.get("data", {})
                .get("biz_data", {})
                .get("challenge", {})
            )
            if not challenge:
                logger.warning(f"PoW 挑战响应中无 challenge: {str(body)[:200]}")
                return None

            logger.debug("PoW 挑战获取成功，开始求解...")

            if self._pow_solver:
                result = self._pow_solver(challenge)
                if result:
                    return result
                logger.warning("PoW 求解失败")
            else:
                logger.error("PoW 求解器未初始化")

            return None

        except Exception as e:
            logger.warning(f"PoW 流程异常: {e}")
            return None

    def _send_chat_request(self, prompt: str, pow_header: str, timeout: int) -> dict:
        """发送聊天请求并解析 SSE 响应"""
        try:
            sess = self._ensure_session()

            referer = f"{DS_BASE}/a/chat/s/{self._session_id}"
            req_headers = {
                "authorization": f"Bearer {self._token}",
                "content-type": "application/json",
                "origin": DS_BASE,
                "referer": referer,
                "user-agent": DS_HEADERS["user-agent"],
                "x-client-version": DS_HEADERS["x-client-version"],
                "x-client-platform": DS_HEADERS["x-client-platform"],
                "accept": "*/*",
                "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            if pow_header:
                req_headers["x-ds-pow-response"] = pow_header

            req_body = {
                "chat_session_id": self._session_id,
                "parent_message_id": None,
                "prompt": prompt,
                "ref_file_ids": [],
                "thinking_enabled": self._thinking_enabled,
                "search_enabled": self._search_enabled,
                "model_type": "default",
            }

            resp = sess.post(
                f"{DS_API}/chat/completion",
                headers=req_headers,
                json=req_body,
                impersonate="chrome120",
                timeout=timeout,
            )

            # 401 → token 过期 → 自动重新登录
            if resp.status_code == 401:
                logger.info("Token 过期，尝试自动重新登录...")
                if self._relogin():
                    # 重新发送
                    return self._send_chat_request(prompt, pow_header, timeout)
                else:
                    logger.error("Token 刷新失败")
                    return {"text": "", "links": []}

            if resp.status_code != 200:
                error_body = resp.text[:300] if hasattr(resp, "text") else ""
                logger.error(
                    f"DeepSeek API 返回 {resp.status_code}: {error_body}"
                )
                return {"text": "", "links": []}

            # 检查 Content-Type：DeepSeek 可能返回 JSON 错误（如 user is muted）
            ct = resp.headers.get("content-type", "")
            if "text/event-stream" not in ct and "application/json" in ct:
                # 可能是业务错误（如账号被禁言/版本过低）
                body_text = resp.text[:500] if hasattr(resp, "text") else ""
                if body_text:
                    try:
                        err_data = json.loads(body_text)
                        inner = err_data.get("data")
                        if inner is None:
                            inner = {}
                        biz_code = inner.get("biz_code", 0)
                        biz_msg = inner.get("biz_msg", "")
                        if biz_code != 0:
                            logger.error(f"DeepSeek 业务错误 (biz_code={biz_code}): {biz_msg}")
                            return {"text": f"[DeepSeek 错误] {biz_msg}", "links": []}
                        # 无 biz_code 的 JSON 错误（如 code=40300 MISSING_HEADER）
                        err_code = err_data.get("code", 0)
                        err_msg = err_data.get("msg", "")
                        if err_code != 0:
                            logger.error(f"DeepSeek API 错误 (code={err_code}): {err_msg}")
                            return {"text": "", "links": []}
                    except json.JSONDecodeError:
                        pass
                logger.warning(f"DeepSeek 返回非 SSE 响应 (Content-Type: {ct}): {body_text[:200]}")
                return {"text": "", "links": []}

            # 解析 SSE 流（返回文本 + 引用链接）
            text, ref_links = self._parse_sse_stream(resp, timeout)

            # 从文本中提取 markdown/HTML 链接
            md_links = self._extract_links(text)

            # 合并去重
            seen_urls = set()
            all_links = []
            for link in ref_links + md_links:
                if link["url"] not in seen_urls:
                    seen_urls.add(link["url"])
                    all_links.append(link)

            logger.info(f"回复获取完成 ({len(text)} 字, {len(all_links)} 个链接)")

            return {"text": text, "links": all_links}

        except Exception as e:
            logger.error(f"聊天请求异常: {e}")
            return {"text": "", "links": []}

    def _parse_sse_stream(self, resp, timeout: int) -> tuple[str, list]:
        """
        解析 DeepSeek SSE 响应，返回 (文本, 引用链接列表)

        从 content fragments 中提取 AI 文本，
        从 results/SET 和 TOOL_OPEN 事件中提取引用链接。

        处理两种格式：
        1. 旧格式: response/thinking_content → response/content → response/status
        2. 新格式: response/fragments（THINK/RESPONSE 类型）
        """
        # 先读取完整响应体（非流式，避免 curl_cffi 兼容问题）
        import io

        collected = []
        fragment_type = None  # THINK / RESPONSE
        ref_links = []        # 从 SSE 事件中收集的引用链接
        seen_refs = set()     # 去重
        start = time.time()

        def _add_ref(url: str, text: str = ""):
            if url and url not in seen_refs:
                seen_refs.add(url)
                ref_links.append({"text": text or url, "url": url})

        # 使用 resp.text 直接获取完整响应体，然后按行切分
        body = resp.text
        if not body:
            logger.warning("SSE 响应体为空")
            return ("", [])

        # 记录前 200 字节用于调试
        logger.debug(f"SSE 原始响应前 200 字节: {body[:200]}")

        # 按行解析
        for line in body.split("\n"):
            line = line.strip()
            if not line:
                continue
            if time.time() - start > timeout:
                logger.warning("SSE 解析超时")
                break

            # 跳过 event: 行、注释行
            if line.startswith("event:") or line.startswith(":") or line == ":":
                continue

            # HTML 错误检测
            if line.startswith("<!DOCTYPE") or line.startswith("<html"):
                logger.error(f"DeepSeek 返回了 HTML: {line[:200]}")
                return ("", [])

            # 非 data: 行
            if not line.startswith("data: "):
                continue

            data = line[6:]  # 去掉 "data: "
            data = data.strip()
            if data in ("", ":", "[DONE]"):
                if data == "[DONE]":
                    break
                continue

            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue

            if not isinstance(obj, dict):
                continue

            val = obj.get("v")

            # ── 顶层错误对象 ──
            if obj.get("type") == "error":
                content = obj.get("content", "")
                logger.warning(f"DeepSeek 返回错误: {content}")
                return ("", [])

            # ── 引用链接事件: response/fragments/-1/results [SET] ──
            if obj.get("p") == "response/fragments/-1/results" and obj.get("o") == "SET":
                if isinstance(val, list):
                    for result in val:
                        if isinstance(result, dict):
                            url = result.get("url", "")
                            title = result.get("title", "")
                            _add_ref(url, title)
                continue

            # ── BATCH 事件（内含 TOOL_OPEN 引用链接） ──
            if obj.get("o") == "BATCH":
                batch_items = val if isinstance(val, list) else [val]
                for item in batch_items:
                    if isinstance(item, dict):
                        # 递归处理 BATCH 中的 fragments
                        inner_v = item.get("v", [])
                        if isinstance(inner_v, list):
                            for frag in inner_v:
                                if isinstance(frag, dict) and frag.get("type") == "TOOL_OPEN":
                                    res = frag.get("result", {})
                                    if isinstance(res, dict):
                                        url = res.get("url", "")
                                        title = res.get("title", "")
                                        _add_ref(url, title)
                continue

            # ── val 是 dict → metadata / fragments ──
            if isinstance(val, dict):
                # v 中的错误
                t_type = val.get("type", "")
                t_content = val.get("content", "")
                if t_type == "error" and t_content:
                    logger.warning(f"回复错误: {t_content}")
                    return ("", [])

                # fragments 格式（从 v.response.fragments 提取）
                resp_data = val.get("response", {})
                if isinstance(resp_data, dict):
                    frags = resp_data.get("fragments", [])
                    if frags and isinstance(frags, list):
                        for frag in frags:
                            if isinstance(frag, dict):
                                ft = frag.get("type", "")
                                if ft:
                                    fragment_type = ft
                                fc = frag.get("content", "")
                                if fc and isinstance(fc, str):
                                    if fragment_type != "THINK":
                                        collected.append(fc)
                continue

            path = obj.get("p", "")

            # ── fragments 追加 ──
            if path == "response/fragments" and isinstance(val, list):
                if val and isinstance(val[-1], dict):
                    last = val[-1]
                    ft = last.get("type", "")
                    if ft:
                        fragment_type = ft
                    fc = last.get("content", "")
                    if fc and isinstance(fc, str):
                        if fragment_type != "THINK":
                            collected.append(fc)
                continue

            # ── fragments content 追加 ──
            if path == "response/fragments/-1/content":
                if fragment_type != "THINK" and isinstance(val, str) and val:
                    collected.append(val)
                continue

            # ── 旧格式: response/content ──
            if path == "response/content":
                if isinstance(val, str) and val:
                    collected.append(val)
                continue

            # ── 旧格式: response/thinking_content（跳过） ──
            if path == "response/thinking_content":
                continue

            # ── 无路径的行（续接） ──
            if not path and isinstance(val, str) and val:
                if fragment_type is not None:
                    if fragment_type != "THINK":
                        collected.append(val)
                else:
                    collected.append(val)
                continue

            # ── 完成状态 ──
            if path == "response/status" and val == "FINISHED":
                break

        result = "".join(collected)
        logger.debug(f"SSE 解析完成: 收集了 {len(result)} 字")
        return (result, ref_links)

    def _extract_links(self, text: str) -> list:
        """从回复 HTML 中提取所有 <a> 标签链接（DeepSeek 回复为 HTML 格式）"""
        from html.parser import HTMLParser

        class _LinkFinder(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links = []
                self._href = ""
                self._text_parts = []
                self._in_a = False
            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self._in_a = True
                    self._text_parts = []
                    d = dict(attrs)
                    self._href = d.get("href", "").strip()
            def handle_data(self, data):
                if self._in_a:
                    self._text_parts.append(data)
            def handle_endtag(self, tag):
                if tag == "a" and self._in_a and self._href:
                    txt = "".join(self._text_parts).strip()
                    self.links.append({"text": txt or self._href, "url": self._href})
                    self._in_a = False

        finder = _LinkFinder()
        finder.feed(text)
        # 去重
        seen = set()
        result = []
        for link in finder.links:
            if link["url"] not in seen:
                seen.add(link["url"])
                result.append(link)
        return result

    # ─── Token 刷新 ─────────────────────────────

    def _relogin(self) -> bool:
        """用保存的密码重新登录，返回是否成功"""
        if not self._account or not self._password:
            logger.error("无保存的账号密码，无法自动重新登录")
            return False

        logger.info("自动重新登录...")
        # 重置 session（清除旧 token 上下文）
        self._session = None
        if self._do_login():
            logger.info("重新登录成功")
            return True

        logger.error("重新登录失败")
        return False

    # ─── 会话管理 ───────────────────────────────

    def save_session(self):
        """保存 token 和 session_id 到文件"""
        self._save_session()

    def _save_session(self):
        """保存登录状态"""
        if not self._token:
            return
        try:
            os.makedirs(SESSION_DIR, exist_ok=True)
            data = {
                "token": self._token,
                "session_id": self._session_id or "",
                "account": self._account,
                "login_type": self._login_type,
                "password": self._password,
                "area_code": self._area_code,
                "saved_at": time.time(),
            }
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"session 已保存到 {TOKEN_FILE}")
        except Exception as e:
            logger.warning(f"保存 session 失败: {e}")

    def _load_session(self) -> bool:
        """加载保存的 token 和 session_id"""
        if not os.path.isfile(TOKEN_FILE):
            logger.debug("未找到已保存的 session 文件")
            return False

        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._token = data.get("token", "")
            self._session_id = data.get("session_id", "")
            self._account = data.get("account", "")
            self._password = data.get("password", "")
            self._login_type = data.get("login_type", "phone")
            self._area_code = data.get("area_code", "+86")

            saved_at = data.get("saved_at", 0)
            age_hours = (time.time() - saved_at) / 3600 if saved_at else 999

            if self._token:
                logger.info(
                    f"加载已保存的 session（账号: {self._account[:4]}****, "
                    f"保存时间: {age_hours:.1f} 小时前）"
                )

            # token 超过 20 小时强制重新登录
            if age_hours > 20:
                logger.info("Token 可能已过期，本次将重新登录")
                return False

            return bool(self._token)

        except Exception as e:
            logger.warning(f"加载 session 失败: {e}")
            return False

    # ─── 资源释放 ───────────────────────────────

    def close(self):
        """关闭 HTTP 会话"""
        try:
            if self._session:
                self._session.close()
                self._session = None
            logger.debug("HTTP 会话已关闭")
        except Exception as e:
            logger.warning(f"关闭会话时出错: {e}")


# ─── 快捷测试 ──────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.info("DeepSeekBot HTTP 版快捷测试 — 完整流程请运行: python -m hotspot")

    bot = DeepSeekBot()
    try:
        bot.start()
        import os as _os
        acc = _os.environ.get("DEEPSEEK_ACCOUNT", "")
        pwd = _os.environ.get("DEEPSEEK_PASSWORD", "")
        if not acc or not pwd:
            logger.error("请设置环境变量 DEEPSEEK_ACCOUNT / DEEPSEEK_PASSWORD")
            import sys
            sys.exit(1)

        if bot.login(acc, pwd):
            bot.enable_all()
            reply = bot.chat("用一句话介绍你自己")
            print(f"\nDeepSeek: {reply}\n")
        else:
            logger.error("登录失败")
    finally:
        bot.close()
