"""
DeepSeek 官方 API 客户端（Responses API + web_search）

纯 HTTP 方案，无需登录/PoW/Session 管理。
使用 api.deepseek.com/responses 端点，内置 web_search 工具。
"""

import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# Responses API 端点
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


def _extract_urls_from_text(text: str) -> list[dict]:
    """从文本中提取所有 http(s):// 链接，返回 [{"text": "...", "url": "..."}, ...]"""
    pattern = r'https?://[^\s\u4e00-\u9fff，。！？、；：""''（）【】《》\]]+'
    urls = []
    seen = set()
    for m in re.finditer(pattern, text):
        url = m.group(0).rstrip(".,;:)")
        if url not in seen:
            seen.add(url)
            urls.append({"text": url, "url": url})
    return urls


class DeepSeekAPI:
    """DeepSeek 官方 API 客户端 — 使用 Responses API + web_search 工具"""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(180, connect=15),
        )
        logger.info(f"DeepSeek API 客户端已初始化 (model={self._model})")

    # ─── 接口兼容方法（与 DeepSeekBot 保持相同签名） ───

    def start(self):
        """加载已保存 session — API 模式无需 session，空操作"""
        pass

    def login(self, account: str = "", password: str = "") -> bool:
        """登录 — API 模式无需登录，始终返回 True"""
        return True

    def enable_all(self):
        """开启深度思考 + 联网搜索 — API 模式在 chat() 中通过 tools 配置"""
        pass

    def new_chat(self):
        """创建新对话 — API 无状态，空操作"""
        pass

    def close(self):
        """关闭连接 — 释放 HTTP 客户端"""
        self._client.close()

    # ─── 核心方法 ───

    def chat(self, prompt: str, timeout: int = 180) -> dict:
        """
        发送 prompt 到 DeepSeek Responses API，返回 {"text": str, "links": list}。

        返回格式与 DeepSeekBot.chat() 完全兼容：
            text  — 模型的 final_answer 文本
            links — 从 open_page 动作和 final_answer 文本中提取的 URL 列表
        """
        body = {
            "model": self._model,
            "input": prompt,
            "tools": [{"type": "web_search"}],
            "stream": False,
        }

        logger.info("API 请求: POST /responses (web_search)")
        try:
            resp = self._client.post("/responses", json=body, timeout=timeout)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"API 请求失败 ({e.response.status_code}): {e.response.text[:500]}")
            return {"text": "", "links": []}
        except httpx.RequestError as e:
            logger.error(f"API 网络错误: {e}")
            return {"text": "", "links": []}

        data = resp.json()

        # 解析 output[] 数组
        final_answer = ""
        open_page_urls: list[dict] = []
        search_count = 0

        for item in data.get("output", []):
            item_type = item.get("type", "")

            if item_type == "message" and item.get("phase") == "final_answer":
                # final_answer 是 content 数组，每个元素可能是 text 或 image
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        final_answer += part.get("text", "")

            elif item_type == "web_search_call":
                action = item.get("action", "")
                if action == "open_page":
                    url = item.get("url", "")
                    if url:
                        open_page_urls.append({"text": url, "url": url})
                elif action == "search":
                    search_count += 1

        # 从 final_answer 文本中提取 URL（模型可能在回答中直接输出链接）
        text_urls = _extract_urls_from_text(final_answer)

        # 合并链接（open_page 优先，文本链接补充）
        seen = set(l["url"] for l in open_page_urls)
        all_links = list(open_page_urls)
        for link in text_urls:
            if link["url"] not in seen:
                seen.add(link["url"])
                all_links.append(link)

        # Token 统计
        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        reasoning_tokens = usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)

        logger.info(
            f"API 响应: {len(final_answer)} 字文本, "
            f"{search_count} 次搜索, {len(open_page_urls)} 个打开页面, "
            f"{len(all_links)} 个链接, {total_tokens} tokens (推理 {reasoning_tokens})"
        )

        return {"text": final_answer, "links": all_links}
