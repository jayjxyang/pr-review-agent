# PR Review Agent

基于 AI 的 GitHub PR 自动 Code Review 机器人。通过 GitHub Webhook 触发，异步分析 PR Diff，将 Review 意见精准挂载到对应代码行。

## 功能

- 监听 GitHub `pull_request` 事件（opened / synchronize）
- HMAC-SHA256 签名验证，防止伪造请求
- `X-GitHub-Delivery` 去重，GitHub 重发时不会触发二次 Review
- Diff 自动过滤（lock 文件、图片、minified 产物等）
- 按 token 上限智能分块，支持超大 PR
- 调用 LLM 生成结构化 Review（文件名、行号、严重程度、评论）
- 行内评论挂载到 PR 对应代码行；行号不在 diff 范围内时自动降级为纯文本评论
- 全链路结构化 JSON 日志（structlog）
- Celery 任务自动重试（最多 3 次，间隔 60s）

## 架构

```
GitHub Webhook
      │
      ▼
┌─────────────┐   HMAC 验签 + 去重
│  FastAPI    │──────────────────────── 202 Accepted
│  /webhook   │
└──────┬──────┘
       │ Celery task
       ▼
┌─────────────────────────────────────┐
│           Celery Worker             │
│                                     │
│  get_pr_patches()                   │
│    └─ PyGithub 拉取 diff，过滤文件   │
│                                     │
│  chunk_diff()                       │
│    └─ tiktoken 计数，贪心分块        │
│                                     │
│  call_llm()  ×  N chunks            │
│    └─ LiteLLM → AI Gateway → LLM   │
│                                     │
│  post_review()                      │
│    └─ 聚合结果，回写 GitHub 评论     │
└─────────────────────────────────────┘
```

## 技术栈

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI + uvicorn |
| 异步任务 | Celery 5 + Redis |
| GitHub 交互 | PyGithub |
| LLM 调用 | LiteLLM（统一多模型接口） |
| Token 计数 | tiktoken（cl100k_base） |
| 结构化日志 | structlog（JSON 输出） |
| 配置管理 | pydantic-settings |

## 项目结构

```
app/
├── main.py                 # FastAPI 入口，lifespan 初始化日志
├── core/
│   ├── config.py           # 环境变量配置（lru_cache 单例）
│   ├── celery_app.py       # Celery 初始化，任务路由
│   ├── dedup.py            # Redis SET NX 幂等去重
│   └── logging.py          # structlog 配置
├── api/
│   └── webhook.py          # Webhook 接收、签名验证、任务投递
├── services/
│   ├── github.py           # 拉取 PR diff，过滤无意义文件
│   ├── chunker.py          # token 计数，贪心分块
│   ├── llm.py              # Prompt 组装，LLM 调用，JSON 解析
│   └── reviewer.py         # 聚合 Review 结果，回写 GitHub 评论
└── tasks/
    └── review.py           # Celery 任务，串联四步流水线
```

## 快速开始

### 前置依赖

- Python 3.10+
- Redis

```bash
# 启动 Redis
docker run -d -p 6379:6379 redis:7
```

### 安装

```bash
pip install -r requirements.txt
```

### 配置

```bash
cp .env.example .env
```

编辑 `.env`，填写以下变量：

| 变量 | 必填 | 说明 |
|------|------|------|
| `GITHUB_WEBHOOK_SECRET` | ✅ | GitHub Webhook 签名密钥 |
| `GITHUB_APP_TOKEN` | ✅ | GitHub Personal Access Token（需要 `pull_requests: write` 权限） |
| `REDIS_URL` | | 默认 `redis://localhost:6379/0` |
| `AI_GATEWAY_URL` | | LLM 网关地址，为空时使用 LiteLLM 默认路由 |
| `LLM_MODEL` | | 默认 `deepseek-chat`，支持任意 LiteLLM 模型名 |
| `DIFF_TOKEN_LIMIT` | | 单次 Diff 最大 token 数，默认 `4000` |

### 启动服务

```bash
# 终端 1：FastAPI
uvicorn app.main:app --reload --port 8000

# 终端 2：Celery Worker（IO 密集型任务，建议用 gevent）
celery -A app.core.celery_app worker --pool=gevent --concurrency=20 --queues=reviews --loglevel=info
```

### 配置 GitHub Webhook

在 GitHub 仓库 → Settings → Webhooks → Add webhook：

- **Payload URL**：`https://your-domain.com/api/v1/webhook/github`
- **Content type**：`application/json`
- **Secret**：填写 `GITHUB_WEBHOOK_SECRET` 的值
- **Events**：勾选 `Pull requests`

本地开发可用 [ngrok](https://ngrok.com) 暴露端口：

```bash
ngrok http 8000
```

## Review 输出格式

每次 Review 以一次 GitHub PR Review 的形式提交，行内评论示例：

> 🔴 **ERROR**: `src/auth.py:42` — SQL query is constructed via string concatenation, vulnerable to injection. Use parameterized queries instead.

> 🟡 **WARNING**: `src/utils.py:17` — Function has cyclomatic complexity of 15. Consider breaking it into smaller functions.

> 🔵 **SUGGESTION**: `src/models.py:88` — This list comprehension could be replaced with a generator expression to reduce peak memory usage.

严重程度说明：

| 图标 | 级别 | 含义 |
|------|------|------|
| 🔴 | error | Bug、安全漏洞、运行时崩溃风险 |
| 🟡 | warning | 代码异味、不良实践 |
| 🔵 | suggestion | 可读性改进、性能优化建议 |

## 可观测性

所有日志以 JSON 格式输出，关键字段：

```json
{"event": "review_started", "repo": "org/repo", "pr": 42, "task_id": "...", "attempt": 1, "timestamp": "..."}
{"event": "chunks_ready", "total": 3}
{"event": "llm_call_done", "chunk": 0, "comments": 5}
{"event": "review_posted", "inline_comments": 12}
```

## 开发说明

### 测试配置注入

`get_settings()` 使用 `lru_cache`，测试时通过 FastAPI 的 `dependency_overrides` 注入测试配置：

```python
app.dependency_overrides[get_settings] = lambda: Settings(
    github_webhook_secret="test-secret",
    ...
)
```

### 切换 LLM 模型

修改 `.env` 中的 `LLM_MODEL`，无需改动代码。LiteLLM 支持的模型名参见 [litellm.ai/docs](https://docs.litellm.ai/docs/providers)。

### Worker 扩容

Review 任务路由到独立的 `reviews` 队列，可单独扩容 worker 数量而不影响其他服务：

```bash
celery -A app.core.celery_app worker --queues=reviews --concurrency=50 --pool=gevent
```
