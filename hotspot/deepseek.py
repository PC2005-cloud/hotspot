"""
DeepSeek Web 自动化模块

功能：
- 自动登录（支持 session 持久化）
- 控制 深度思考 / 智能搜索 开关
- 后续可扩展对话功能
"""

import os
import json
import logging
from typing import Optional

from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
)

logger = logging.getLogger(__name__)

# ─── 路径配置 ───────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROWSERS_DIR = os.path.join(BASE_DIR, "browsers")
SESSION_DIR = os.path.join(BASE_DIR, "session")
SESSION_FILE = os.path.join(SESSION_DIR, "storage_state.json")
COOKIE_FILE = os.path.join(BASE_DIR, "deepseek_cookies.json")

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", BROWSERS_DIR)

# ─── 反检测脚本 ─────────────────────────────────────

ANTI_DETECTION = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
        { name: 'Native Client', filename: 'pnacl' },
    ],
});
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
window.chrome = window.chrome || {};
window.chrome.runtime = {};
"""

# ─── 浏览器管理器 ───────────────────────────────────

class DeepSeekBot:
    """DeepSeek 网页自动化机器人"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._page: Optional[Page] = None
        self._context: Optional[BrowserContext] = None
        self._browser: Optional[Browser] = None
        self._playwright = None

    # ---- 启动与登录 ----

    def start(self) -> Page:
        """启动浏览器并恢复 session（如有），返回 Page"""
        try:
            self._playwright = sync_playwright().start()
        except Exception as e:
            logger.error(f"Playwright 引擎启动失败: {e}")
            raise

        try:
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception as e:
            logger.error(f"Chromium 浏览器启动失败 (headless={self.headless}): {e}")
            raise

        storage = self._load_session()
        self._context = self._browser.new_context(storage_state=storage or None)
        self._page = self._context.new_page()
        self._page.add_init_script(ANTI_DETECTION)

        self._page.set_default_timeout(30000)
        return self._page

    def login(self, account: str, password: str) -> bool:
        """登录 DeepSeek，返回是否登录成功"""
        page = self._page
        try:
            page.goto("https://chat.deepseek.com")
        except Exception as e:
            logger.error(f"访问 DeepSeek 页面失败: {e}")
            return False
        page.wait_for_timeout(5000)
        logger.info(f"页面URL: {page.url}")
        logger.info(f"页面标题: {page.title()}")

        # 检测 Cloudflare 拦截
        if "ERROR" in page.title() or "request could not be satisfied" in page.content()[:500]:
            logger.error("❌ Cloudflare 拦截：无法访问 DeepSeek（CI 环境网络受限）")
            return False

        # 如果已有 session 直接进入聊天页
        if "sign_in" not in page.url:
            logger.info("已有有效 session，跳过登录")
            # 检测封禁
            banned = page.locator(".ds-alert__content:has-text(\"违反\")")
            if banned.count() > 0:
                msg = banned.first.text_content() or ""
                logger.error(f"❌ 账号被封禁: {msg}")
                return False
            return True

        logger.info("需要登录...")
        # 切换到密码登录
        page.get_by_role("button", name="密码登录").first.click()
        page.wait_for_timeout(1000)

        # 填账号密码
        try:
            page.locator('input[type="text"]').fill(account)
            page.locator('input[type="password"]').fill(password)
            page.locator("div.ds-button--filled").first.click()
        except Exception as e:
            logger.error(f"登录表单填写或提交失败: {e}")
            return False
        page.wait_for_timeout(8000)

        if "sign_in" in page.url:
            logger.error("登录失败，请检查账号密码")
            return False

        # 保存 session（登录已成功，凭证有效）
        self.save_session()

        # 检测账号是否被封禁
        banned = page.locator(".ds-alert__content:has-text(\"违反\")")
        if banned.count() > 0:
            msg = banned.first.text_content() or ""
            logger.error(f"❌ 账号被封禁: {msg}")
            return False
        logger.info("登录成功")
        return True

    # ---- 按钮控制 ----

    def set_toggle(self, name: str, target_on: bool = True):
        """设置 深度思考/智能搜索 按钮状态"""
        btn = self._page.locator(f'div.ds-toggle-button:has-text("{name}")')
        if btn.count() == 0:
            logger.warning(f"[{name}] 未找到按钮")
            return
        is_on = btn.get_attribute("aria-pressed") == "true"
        if is_on != target_on:
            btn.click()
            self._page.wait_for_timeout(500)
            logger.info(f"[{name}] 已{'开启' if target_on else '关闭'}")
        else:
            logger.info(f"[{name}] 已经是{'开启' if target_on else '关闭'}状态")

    def enable_all(self):
        """开启深度思考和智能搜索"""
        for name in ["深度思考", "智能搜索"]:
            self.set_toggle(name, target_on=True)

    def disable_all(self):
        """关闭深度思考和智能搜索"""
        for name in ["深度思考", "智能搜索"]:
            self.set_toggle(name, target_on=False)

    # ---- 对话 ----

    def delete_chat(self):
        """删除当前对话（清理侧边栏历史）"""
        # 检查配置是否允许删除
        _cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hotspot", "config.json")
        try:
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                if not json.load(_f).get("delete_chat", True):
                    return
        except Exception:
            pass

        import re
        m = re.search(r'([a-f0-9-]{36})', self._page.url)
        chat_id = m.group(1) if m else ""
        if not chat_id:
            return

        try:
            # 点击侧边栏当前对话的更多按钮
            btn = self._page.locator(f'a[href*="{chat_id}"] div.ds-button')
            if btn.count() == 0:
                return
            btn.first.click()
            self._page.wait_for_timeout(500)

            # 点击「删除」
            delete_btn = self._page.locator(
                'div.ds-dropdown-menu-option__label:text("删除")'
            )
            if delete_btn.count() == 0:
                return
            delete_btn.first.click()
            self._page.wait_for_timeout(500)

            # 确认删除（如有弹窗）
            confirm = self._page.locator('button:has-text("删除"), div.ds-button:has-text("删除")').last
            if confirm.count() > 0:
                confirm.click()
                self._page.wait_for_timeout(500)

            logger.debug(f"已删除对话: {chat_id}")
        except Exception as e:
            logger.warning(f"删除对话失败: {e}")

    def new_chat(self):
        """开启新对话（先删除当前对话，再开新对话）"""
        logger.info("准备开启新对话...")
        self.delete_chat()
        try:
            self._page.goto("https://chat.deepseek.com")
        except Exception as e:
            logger.error(f"导航到 DeepSeek 首页失败: {e}")
            raise
        self._page.wait_for_timeout(2000)
        logger.info("新对话已就绪")

    def chat(self, message: str, timeout: int = 120) -> dict:
        """
        发送消息并等待回复完成（等内容不再变化）

        参数：
            message: 要发送的消息
            timeout: 等待回复的最大秒数

        返回：
            {"text": "完整回复文本", "links": [{"text": "链接文字", "url": "链接地址"}, ...]}
        """
        msg_preview = message[:80].replace("\n", " ")
        logger.info(f"发送消息 ({len(message)} 字, 超时 {timeout}秒): {msg_preview}...")
        textarea = self._page.locator('textarea[name="search"]')
        textarea.fill(message)
        self._page.wait_for_timeout(300)
        textarea.press("Enter")

        import time
        start = time.time()
        reply = self._page.locator("div.ds-markdown.ds-assistant-message-main-content")
        last_len = 0
        stable_polls = 0

        while time.time() - start < timeout:
            if reply.count() > 0:
                text = reply.last.text_content() or ""
                current_len = len(text.strip())

                if current_len > 3:
                    if current_len == last_len:
                        stable_polls += 1
                    else:
                        stable_polls = 0

                    if stable_polls >= 3:
                        elapsed = time.time() - start
                        logger.info(f"回复完成 ({elapsed:.1f}秒, {current_len}字)")
                        return self._get_reply_with_links()

                    last_len = current_len

            self._page.wait_for_timeout(500)

        if reply.count() > 0:
            logger.warning(f"回复等待超时 ({timeout}秒)，但存在部分回复，尝试提取")
            return self._get_reply_with_links()
        logger.warning(f"回复等待超时 ({timeout}秒)，未获取到任何回复")
        return {"text": "", "links": []}

    def _get_reply_with_links(self) -> dict:
        """获取回复文本及引用链接"""
        reply = self._page.locator("div.ds-markdown.ds-assistant-message-main-content")
        if reply.count() == 0:
            logger.warning("未找到 AI 回复元素，可能页面加载异常或回复被拦截")
            return {"text": "", "links": []}

        text = reply.last.text_content().strip() or ""

        if not text and reply.count() > 0:
            logger.warning("AI 回复元素存在但内容为空")

        # 从 HTML 中提取链接
        from bs4 import BeautifulSoup
        html = reply.last.inner_html()
        soup = BeautifulSoup(html, "lxml")
        links = []
        seen = set()
        for a in soup.find_all("a"):
            href = a.get("href", "").strip()
            txt = a.text.strip()
            if href and href not in seen:
                seen.add(href)
                links.append({"text": txt or href, "url": href})

        if links:
            logger.info(f"从回复中提取到 {len(links)} 个引用链接")
        return {"text": text, "links": links}

    # ---- 会话管理 ----

    def save_session(self):
        """保存登录状态到文件"""
        if not self._context:
            logger.warning("浏览器上下文未初始化，跳过 session 保存")
            return
        try:
            os.makedirs(SESSION_DIR, exist_ok=True)
            self._context.storage_state(path=SESSION_FILE)
            logger.info(f"session 已保存到 {SESSION_FILE}")
        except Exception as e:
            logger.error(f"保存 session 失败: {e}")

    def _load_session(self) -> Optional[str]:
        """读取已保存的 session 文件路径"""
        if os.path.isfile(SESSION_FILE):
            logger.info(f"发现已保存的 session: {SESSION_FILE}")
            return SESSION_FILE
        logger.info("未找到已保存的 session，需要登录")
        return None

    # ---- 资源释放 ----

    def close(self):
        """关闭浏览器"""
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as e:
            logger.warning(f"关闭浏览器时出错: {e}")


# ─── 快捷测试 ──────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    bot = DeepSeekBot(headless=False)
    try:
        page = bot.start()
        bot.login("13092297340", "20050816Pc..")
        bot.enable_all()

        reply = bot.chat("用一句话介绍你自己")
        print(f"\nDeepSeek: {reply}\n")

        input("按回车退出...")
    finally:
        bot.close()
