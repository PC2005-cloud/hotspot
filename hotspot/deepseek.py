"""
DeepSeek Web 自动化模块

功能：
- 自动登录（支持 session 持久化）
- 控制 深度思考 / 智能搜索 开关
- 后续可扩展对话功能
"""

import os
import sys
import json
import random
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
// ─── 1. 隐藏自动化标记 ───
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// ─── 2. 伪造 Chrome 运行时对象 ───
window.chrome = {
    runtime: {
        onMessage: { addListener: function() {} },
        onConnect: { addListener: function() {} },
        onInstalled: { addListener: function() {} },
    },
    loadTimes: function() {},
    csi: function() {},
    app: { isInstalled: false },
};

// ─── 3. 补全插件列表 ───
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const p = [
            { name: 'Chrome PDF Plugin',     filename: 'internal-pdf-viewer',                 description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer',      filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',    description: '' },
            { name: 'Native Client',          filename: 'pnacl',                               description: '' },
        ];
        p.__proto__ = PluginArray.prototype;
        return p;
    },
});
Object.defineProperty(navigator, 'mimeTypes', {
    get: () => {
        const m = [];
        m.__proto__ = MimeTypeArray.prototype;
        return m;
    },
});

// ─── 4. 语言与时区 ───
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });

// ─── 5. 硬件指纹 ───
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });

// ─── 6. 权限 API（仅拦截已知会被检测的权限）───
const _origQuery = navigator.permissions.query.bind(navigator.permissions);
navigator.permissions.query = (params) => {
    if (params.name === 'notifications')
        return Promise.resolve({ state: 'prompt', onchange: null });
    if (params.name === 'clipboard-read' || params.name === 'clipboard-write')
        return Promise.resolve({ state: 'granted', onchange: null });
    return _origQuery(params);
};

// ─── 7. WebGL 供应商掩盖 ───
const _origGLGetParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return _origGLGetParam.call(this, p);
};

// ─── 8. 网络连接信息 ───
Object.defineProperty(navigator, 'connection', {
    get: () => ({
        effectiveType: '4g', rtt: 50, downlink: 10, saveData: false,
        addEventListener: function() {}, removeEventListener: function() {},
    }),
});
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
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-networking",
                    "--disable-sync",
                ],
            )
        except Exception as e:
            logger.error(f"Chromium 浏览器启动失败 (headless={self.headless}): {e}")
            raise

        storage = self._load_session()
        self._context = self._browser.new_context(
            storage_state=storage or None,
            viewport={"width": 1920, "height": 1080},
            timezone_id="Asia/Shanghai",
            locale="zh-CN",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            geolocation={"latitude": 31.2304, "longitude": 121.4737},
            permissions=["geolocation"],
            color_scheme="light",
            device_scale_factor=1,
            extra_http_headers={
                "sec-ch-ua": '"Chromium";v="131", "Not(A:Brand";v="24"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        logger.info(f"浏览器上下文: 1920x1080, Asia/Shanghai, zh-CN, headless={self.headless}, geolocation=上海")
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
        page.wait_for_timeout(random.uniform(4000, 7000))
        # 模拟人类鼠标移动和滚动
        page.mouse.move(random.uniform(100, 500), random.uniform(100, 500))
        page.evaluate(f"window.scrollTo(0, {random.randint(50, 200)})")
        page.wait_for_timeout(random.uniform(300, 700))
        logger.debug("页面加载后模拟鼠标移动和滚动完成")
        logger.info(f"页面URL: {page.url}")
        logger.info(f"页面标题: {page.title()}")

        # 检测 Cloudflare 拦截或验证码
        page_title = page.title()
        page_content_preview = page.content()[:800].lower()
        if "ERROR" in page_title or "request could not be satisfied" in page_content_preview:
            logger.error("❌ Cloudflare 拦截：无法访问 DeepSeek（CI 环境网络受限）")
            return False
        if "just a moment" in page_content_preview or "cf-challenge" in page_content_preview:
            logger.error("❌ Cloudflare 验证码挑战中，需手动处理")
            return False

        # 如果已有 session 直接进入聊天页
        if "sign_in" not in page.url:
            logger.info("已有有效 session，跳过登录")
            # 检测封禁
            banned_el = page.locator(".ds-alert__content:has-text(\"违反\")")
            if banned_el.count() > 0:
                msg = banned_el.first.text_content() or ""
                logger.error(f"❌ 账号被封禁: {msg}")
                return False
            return True

        logger.info("需要登录...")
        # 先检查是否已在登录页
        page.wait_for_timeout(random.uniform(1000, 2000))
        # 切换到密码登录
        try:
            pw_btn = page.get_by_role("button", name="密码登录")
            if pw_btn.count() > 0:
                page.mouse.move(random.uniform(200, 600), random.uniform(200, 500))
                page.wait_for_timeout(random.uniform(100, 300))
                pw_btn.first.click()
                page.wait_for_timeout(random.uniform(800, 1500))
                page.evaluate(f"window.scrollTo(0, {random.randint(0, 100)})")
        except Exception as e:
            logger.warning(f"切换到密码登录页可能失败: {e}")

        # 填账号密码（模拟人类逐字段填写间隔 + 键盘事件）
        try:
            page.locator('input[type="text"]').press_sequentially(account, delay=random.randint(20, 50))
            page.wait_for_timeout(random.uniform(300, 800))
            page.locator('input[type="password"]').press_sequentially(password, delay=random.randint(20, 50))
            page.wait_for_timeout(random.uniform(200, 600))
            # 模拟点击登录前移动鼠标
            page.mouse.move(random.uniform(300, 700), random.uniform(300, 600))
            page.wait_for_timeout(random.uniform(100, 300))
            page.locator("div.ds-button--filled").first.click()
        except Exception as e:
            logger.error(f"登录表单填写或提交失败: {e}")
            return False
        page.wait_for_timeout(random.uniform(6000, 10000))
        logger.debug("登录按钮已点击，等待响应...")

        if "sign_in" in page.url:
            logger.error("登录失败，请检查账号密码")
            return False

        # 保存 session（登录已成功，凭证有效）
        self.save_session()

        # 检测账号是否被封禁
        banned_el = page.locator(".ds-alert__content:has-text(\"违反\")")
        if banned_el.count() > 0:
            msg = banned_el.first.text_content() or ""
            logger.error(f"❌ 账号被封禁: {msg}")
            return False
        logger.info("✅ 登录成功")
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
            # 模拟人类点击前移动鼠标
            self._page.mouse.move(random.uniform(300, 700), random.uniform(200, 500))
            self._page.wait_for_timeout(random.uniform(100, 300))
            btn.click()
            self._page.wait_for_timeout(random.uniform(300, 800))
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
            self._page.mouse.move(random.uniform(200, 600), random.uniform(200, 500))
            self._page.wait_for_timeout(random.uniform(100, 300))
            btn.first.click()
            self._page.wait_for_timeout(random.uniform(400, 900))

            # 点击「删除」
            delete_btn = self._page.locator(
                'div.ds-dropdown-menu-option__label:text("删除")'
            )
            if delete_btn.count() == 0:
                return
            self._page.mouse.move(random.uniform(200, 600), random.uniform(300, 550))
            self._page.wait_for_timeout(random.uniform(100, 300))
            delete_btn.first.click()
            self._page.wait_for_timeout(random.uniform(400, 900))

            # 确认删除（如有弹窗）
            confirm = self._page.locator('button:has-text("删除"), div.ds-button:has-text("删除")').last
            if confirm.count() > 0:
                self._page.mouse.move(random.uniform(300, 650), random.uniform(350, 600))
                self._page.wait_for_timeout(random.uniform(100, 300))
                confirm.click()
                self._page.wait_for_timeout(random.uniform(400, 900))

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
        self._page.wait_for_timeout(random.uniform(1500, 3000))
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
        # 模拟人类输入后停顿思考再发送
        self._page.wait_for_timeout(random.uniform(500, 2000))
        # 发送前移动鼠标到页面其他位置
        self._page.mouse.move(random.uniform(400, 900), random.uniform(100, 400))
        self._page.wait_for_timeout(random.uniform(200, 500))
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

            self._page.wait_for_timeout(random.uniform(400, 700))

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
    logger.info("DeepSeekBot 快捷测试 — 完整流程请运行: python -m hotspot")

    bot = DeepSeekBot(headless=False)
    try:
        page = bot.start()
        import os as _os
        acc = _os.environ.get("DEEPSEEK_ACCOUNT", "")
        pwd = _os.environ.get("DEEPSEEK_PASSWORD", "")
        if not acc or not pwd:
            logger.error("请设置环境变量 DEEPSEEK_ACCOUNT / DEEPSEEK_PASSWORD 传入测试账号")
            sys.exit(1)
        bot.login(acc, pwd)
        bot.enable_all()

        reply = bot.chat("用一句话介绍你自己")
        print(f"\nDeepSeek: {reply}\n")

        input("按回车退出...")
    finally:
        bot.close()
