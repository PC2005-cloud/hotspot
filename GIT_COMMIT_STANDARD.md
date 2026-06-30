# Git 提交规范（企业级）

## 提交格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

每次提交由 **header**、**body**、**footer** 三部分组成，header 为必填。

## 提交类型（type）

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | 修复 bug |
| `docs` | 文档变更 |
| `style` | 代码格式调整（不影响功能） |
| `refactor` | 重构（既不是新功能也不是修 bug） |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `build` | 构建系统或外部依赖变更 |
| `ci` | CI/CD 配置变更 |
| `chore` | 杂项（构建流程、辅助工具等） |
| `revert` | 回退 |

## 作用域（scope）

可选，表示影响范围：

```
feat(hotspotter): 添加 URL 验证功能
fix(cli): 修复日志路径错误
ci(workflow): 修改 GitHub Actions 触发条件
```

## 提交说明（subject）

- 不超过 50 个字符
- 中文或英文，中文优先
- 结尾不加句号
- 祈使句，说明「做了什么」

## 正文（body）

- 每行不超过 72 字
- 说明「为什么这么做」而不是「做了什么」
- 与 header 之间空一行

## 尾部（footer）

- 不兼容变更：`BREAKING CHANGE: xxx`
- 关闭 Issue：`Closes #123, #456`

## 分支命名

| 分支类型 | 格式 |
|---------|------|
| 主分支 | `master` / `main` |
| 功能分支 | `feat/xxx` |
| 修复分支 | `fix/xxx` |
| 发布分支 | `release/v1.0.0` |

## 示例

```
feat(searcher): 添加 URL 可用性验证功能

对 DeepSeek 返回的热点链接进行 HEAD 请求验证，
自动过滤 404 和超时链接，确保结果中的 URL 真实可访问。

Closes #42
```

```
fix(login): 修复 headless 模式下登录检测

Cloudflare 拦截页面不含 sign_in 路径，
改为检测页面标题和输入框存在性来判断登录状态。

Closes #18
```

```
ci(workflow): 配置 GitHub Actions 定时触发

添加每天早上 8 点和晚上 8 点的定时任务，
推送、定时、手动三种方式均可触发部署。
```
