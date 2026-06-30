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
- **深度思考 + 智能搜索** — 自动开启 DeepSeek 联网搜索
- **去重去广告** — 相同 URL 只保留一条，过滤无关内容
- **按时间排序** — 从摘要中解析发布时间，最新热点排最前
- **多角度观点提取** — 每条热点自动整理不同立场和争议观点
- **舆情总结** — AI 自动生成舆情趋势分析
- **URL 验证** — HEAD 请求检测每个链接，404 自动剔除
- **引用链接替换** — 回复中的引用标记自动匹配真实链接
- **JSON + HTML 双报告** — 结构化数据 + 可视化页面，按日期/时间分目录保存
- **Session 持久化** — 首次登录后自动保存，后续无需重复登录
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
  "delete_chat": true,
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
| `headless` | `true` | 无头模式（不显示浏览器窗口） |
| `delete_chat` | `true` | 搜索后删除对话，清理侧边栏 |
| `login.account` | `""` | DeepSeek 登录手机号 |
| `login.password` | `""` | DeepSeek 登录密码 |
| `keywords` | `[]` | 要搜索的关键词列表 |
| `prompt` | 内置模板 | AI 提示词，自定义搜索指令 |

## 工作流程

```
启动程序
  ↓
读取 keywords.json
  ↓
启动浏览器（Playwright）
  ↓
登录 DeepSeek（session 有效则跳过）
  ↓
开启 深度思考 + 智能搜索
  ↓
遍历每个关键词:
  ├─ 发提示词给 DeepSeek
  ├─ 等回复 → 提取 JSON 热点数据
  ├─ JSON 解析失败？重试（最多3次）
  ├─ 匹配引用链接 → 填补无效 URL
  ├─ 清理摘要中的标记符号
  ├─ URL 去重 + 可用性验证
  └─ 保存 {关键词}.json
  ↓
聚合所有关键词结果
  ↓
生成 HTML 报告 + 部署到 gh-pages
```

## 输出结构

```
results/
└── 2026-06-30/
    └── 143000/
        ├── run.log
        ├── report.json
        ├── report.html
        ├── DeepSeek.json
        └── ...
```

## 本地部署

GitHub Actions 服务器在美国，访问 DeepSeek 会被 Cloudflare 拦截，所以流水线实际上跑不通。  
需要在你的电脑上运行，再把结果推送到 gh-pages。

运行 `deploy.bat` 一键完成：

```bash
# 直接双击 deploy.bat，或在终端运行
deploy.bat
```

执行流程：
- 清理上次结果
- 运行热点搜索
- 检查是否有热点数据（无数据则跳过部署，保留上次报告）
- 有数据则推送到 gh-pages 分支，更新网站

## GitHub Actions

> ⚠️ 注意：GitHub Actions 服务器在美国，访问 DeepSeek 可能被 Cloudflare 拦截。建议使用[自托管 runner](https://docs.github.com/zh/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners) 或在本地运行。

流水线在以下时机触发：
- **推送代码** — 推送到 `master` 时自动运行
- **定时触发** — 每天 08:00 / 20:00（北京时间）
- **手动触发** — GitHub 页面点击 "Run workflow"

运行结果自动部署到 `gh-pages` 分支。

### 修改运行周期

编辑 `.github/workflows/hotspot.yml` 中的 `schedule` 字段：

```yaml
schedule:
  - cron: "0 0 * * *"    # UTC 0:00 = 北京时间 8:00
  - cron: "0 12 * * *"   # UTC 12:00 = 北京时间 20:00
```

cron 表达式格式：`分 时 日 月 周`

| 示例 | 说明 |
|------|------|
| `0 0 * * *` | 每天 UTC 0:00（北京时间 8:00） |
| `0 */6 * * *` | 每 6 小时一次 |
| `30 1 * * 1-5` | 工作日 9:30（UTC+8） |

## 项目结构

```
hotspot/
├── cli.py          # 入口（日志、汇总、调用流程）
├── deepseek.py     # 浏览器自动化（Playwright）
├── hotspotter.py   # 搜索逻辑（提示词、解析、验证、报告）
├── config.json     # 配置
├── template.html   # HTML 报告模板
└── __main__.py     # python -m 入口
deploy.bat          # 本地一键部署脚本
```
