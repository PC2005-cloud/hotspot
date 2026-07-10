# Hotspot

零成本热点追踪 · 基于 DeepSeek 免费网页版 · 告别 RSS 噪音和搜索广告

![CI](https://img.shields.io/github/actions/workflow/status/PC2005-cloud/hotspot/hotspot.yml?branch=master)
![部署](https://img.shields.io/github/deployments/PC2005-cloud/hotspot/github-pages?label=Pages)
![Python](https://img.shields.io/badge/Python-3.12-blue)
![MIT](https://img.shields.io/badge/license-MIT-green)
![最后提交](https://img.shields.io/github/last-commit/PC2005-cloud/hotspot)

AI 每天自动刷热点、总结观点、生成报告。

**示例网站**: [https://hotspot.lxpavilion.top](https://hotspot.lxpavilion.top)

---

## 功能

- **多关键词批量搜索** — 一次配置，自动遍历所有关键词
- **深度思考 + 联网搜索** — 自动开启 DeepSeek 深度思考与联网搜索
- **纯 HTTP 方案** — 直接调用 DeepSeek 网页版内部 API，无需浏览器、无指纹检测风险
- **去重去广告** — 相同 URL 只保留一条，过滤无关内容
- **多角度观点提取** — 每条热点自动整理不同立场和争议观点
- **舆情总结** — AI 自动生成舆情趋势分析
- **URL 验证** — HEAD 请求检测每个链接，404 自动剔除
- **引用链接提取** — 从 SSE 流的搜索结果事件中自动提取引用链接，填补 null URL
- **JSON + HTML 双报告** — 结构化数据 + 可视化页面，按日期/时间分目录保存
- **Session 持久化** — 登录 Token 自动保存，24 小时内无需重复登录
- **Token 过期自动刷新** — 检测到 401 自动用密码重新登录
- **失败重试** — JSON 解析失败自动重试，最多 3 次

## 快速开始

```bash
# 安装依赖
uv sync

# 运行
uv run python -m hotspot
```

自动登录 DeepSeek，Session 持久化，后续运行无需重复登录。

## 配置

编辑 `hotspot/config.json`：

```json
{
  "max_retries": 3,
  "headless": true,
  "login": {
    "account": "138xxxxxxxx",
    "password": "your_password"
  },
  "keywords": ["DeepSeek", "ChatGPT", "Rust"]
}
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_retries` | `3` | JSON 解析失败重试次数 |
| `headless` | `true` | 保留兼容，不再使用（纯 HTTP 无需浏览器） |
| `login.account` | `""` | DeepSeek 登录手机号 |
| `login.password` | `""` | DeepSeek 登录密码 |
| `keywords` | `[]` | 要搜索的关键词列表 |
| `prompt` | 内置模板 | AI 提示词，自定义搜索指令 |

## 技术架构

不再使用 Playwright 浏览器自动化，改为**纯 HTTP 方案**：

```
┌──────────────────────────────┐
│          Hotspot CLI         │
│  hotspot/cli.py              │
└──────────┬───────────────────┘
           │
           ▼
┌──────────────────────────────┐
│    HTTP API 客户端           │
│  hotspot/deepseek.py         │
│                              │
│  ├─ 账号密码登录             │
│  │  POST /api/v0/users/login │
│  ├─ 创建聊天 Session         │
│  │  POST /api/v0/chat_session/create
│  ├─ PoW 挑战求解             │
│  │  (wasmtime 加载官方 WASM) │
│  └─ 流式对话                 │
│     POST /api/v0/chat/completion
│     SSE → 解析 fragments     │
└──────────┬───────────────────┘
           │
           ▼
┌──────────────────────────────┐
│    搜索逻辑管线              │
│  hotspot/hotspotter.py       │
│                              │
│  ├─ 构建 prompt              │
│  ├─ JSON 双解析(json+demjson)│
│  ├─ 引用链接匹配填补         │
│  ├─ URL 去重                 │
│  ├─ HEAD 请求验证 URL 可用性  │
│  └─ 保存 {关键词}.json       │
└──────────┬───────────────────┘
           │
           ▼
┌──────────────────────────────┐
│    报告生成                  │
│  report.json + report.html   │
│  → 部署到 gh-pages           │
└──────────────────────────────┘
```

### 反检测机制

| 技术 | 说明 |
|------|------|
| curl-cffi | 模拟 Chrome 134 TLS 指纹（SSL 握手层） |
| WASM PoW | 加载官方 PoW WASM 二进制求解，请求头与网页版一致 |
| 请求头伪造 | User-Agent、x-client-version、Referer 等与真实浏览器一致 |
| SSE 流解析 | 非流式缓冲全文，片段/fragments 双格式兼容 |

与旧的 Playwright 方案相比：**不再暴露 headless Chromium 的浏览器指纹**（navigator.webdriver、Canvas、WebGL 等），请求特征与真实网页版用户无法区分。

## 工作流程

```
启动程序
  ↓
读取 config.json
  ↓
POST /api/v0/users/login（session 有效则跳过）
  ↓
POST /api/v0/chat_session/create
  ↓
遍历每个关键词:
  ├─ 获取 + 求解 PoW 挑战（wasmtime 加载官方 WASM，~0.5s）
  ├─ POST /api/v0/chat/completion（SSE 流）
  │  ├─ 从 content fragments 提取文本
  │  └─ 从 results/SET + TOOL_OPEN 事件提取引用链接
  ├─ JSON 解析失败？重试（最多3次）
  ├─ 引用链接匹配 → 填补 null URL
  ├─ 清理摘要中的标记符号
  ├─ URL 去重 + HEAD 可用性验证
  └─ 保存 {关键词}.json
  ↓
聚合所有关键词结果
  ↓
生成 HTML 报告 + 部署到 gh-pages
```

## 输出结构

```
results/
└── 2026-07-09/
    └── 143000/
        ├── run.log
        ├── report.json
        ├── report.html
        ├── DeepSeek.json
        └── ...
```

## 本地部署

```bash
# 直接双击 deploy.bat，或在终端运行
deploy.bat
```

执行流程：
- 运行热点搜索
- 检查是否有热点数据（无数据则跳过部署，保留上次报告）
- 有数据则推送到 gh-pages 分支，更新网站

## GitHub Actions

流水线在以下时机触发：
- **推送代码** — 推送到 `master` 时自动运行
- **定时触发** — 每周一早 8:00（北京时间）
- **手动触发** — GitHub 页面点击 "Run workflow"

运行结果自动部署到 `gh-pages` 分支。

### 修改运行周期

编辑 `.github/workflows/hotspot.yml` 中的 `schedule` 字段：

```yaml
schedule:
  - cron: "0 0 * * 1"    # 每周一 UTC 0:00 = 北京时间 8:00
```

cron 表达式格式：`分 时 日 月 周`

| 示例 | 说明 |
|------|------|
| `0 0 * * 1` | 每周一 8:00（北京时间） |
| `0 0 * * *` | 每天 8:00（北京时间） |
| `0 */6 * * *` | 每 6 小时一次 |

## 项目结构

```
hotspot/
├── cli.py            # 入口（日志、汇总、调用流程）
├── deepseek.py       # HTTP API 客户端（登录、PoW、SSE 解析）
├── deepseek_pow.py   # PoW 求解器（wasmtime 加载官方 WASM）
├── sha3_wasm_bg.wasm # DeepSeek 官方 PoW WASM 二进制
├── hotspotter.py     # 搜索逻辑（提示词、解析、验证、报告）
├── config.json       # 配置
├── template.html     # HTML 报告模板
└── __main__.py       # python -m 入口
deploy.bat            # 本地一键部署脚本
```
