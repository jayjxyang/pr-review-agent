# PR Review Agent

基于 **LangGraph ReAct Agent** 的 GitHub PR 自动 Code Review 守门员。

GitHub Webhook 触发 → Celery 异步派发 → LangGraph Agent 自主循环：按需读源码、查历史、跑检查，输出风险判定与行内评论，并通过 Check Run API 可阻断合并。

> 定位：不是帮作者"写得更好"，而是帮团队"拦住风险、看见全局"——聚焦人工 Review 易遗漏的跨模块集成风险、行为变更、安全合规与协作盲区。

---

## 核心特性

- **GitHub App 认证 + Check Run**：可阻断合并（advisory / enforced 可配），不只是发 comment
- **LangGraph Agent 自主循环**：16 个工具，Agent 自己决定"看什么、看多深"
- **双模型风险路由**：低/中风险用 Flash 快速出 review，Agent 判定 high risk 时升级 Sonnet 深度分析
- **安全旁路**：密钥扫描独立于 LLM 循环，结果不可被 Agent 覆盖
- **Re-review 增量**：新 push 后带着"上次提了什么"增量评审，自动标记已修复问题
- **副作用幂等**：at-least-once 重试下不重复建 Check Run / 不重复发评论
- **开发者反馈**：👍/👎 emoji 自动回收，为误报抑制铺路

---

## 端到端流程

```
开发者提交 PR / 推送新 commit
        │
        ▼
GitHub 发送 Webhook（POST /api/v1/webhook/github）
        │
        ▼
┌─ Phase 1：Webhook 接收层（FastAPI）─────────────────┐
│  - HMAC-SHA256 签名验证                              │
│  - X-GitHub-Delivery 去重（Redis SET NX）            │
│  - 过滤事件类型（仅 opened/synchronize）              │
│  - 从 payload 取 head.sha（钉住触发事件的 commit）    │
│  - 立即返回 202 Accepted                             │
│  - 投递 Celery 任务（透传 head_sha + delivery_id）   │
└───────────────┬──────────────────────────────────────┘
                ▼
┌─ Phase 2：Pre-graph 准备 ───────────────────────────┐
│  - collect_feedback() — 收集上次评论的 emoji 反馈     │
│  - create_check_run() — 创建 in_progress Check Run   │
│  - run_secret_scan() — 独立密钥扫描（LLM 不可覆盖）  │
│  - 加载 re-review 上下文（prior_comments）            │
└───────────────┬──────────────────────────────────────┘
                ▼
┌─ Phase 3：LangGraph Agent 循环 ─────────────────────┐
│  scan_call → scan_tools → post_tool_processing       │
│       ↑           │                                  │
│       └───────────┘  ← tools_router 条件路由         │
│  终止：finish_review / escalate→deep_review /         │
│        max_rounds / token 预算 / 死循环检测          │
│  第 5/10/15 轮触发上下文压缩 → 继续循环               │
└───────────────┬──────────────────────────────────────┘
                ▼
┌─ Phase 4：结果处理（全程幂等）─────────────────────┐
│  - compute_conclusion() — 综合安全扫描 + 风险等级     │
│  - update_check_run() — 写 Check Run 结论 + 注解     │
│  - 持久化到 PostgreSQL（按 repo:pr:sha 幂等 upsert） │
│  - Re-review 时标记旧 comments 为 resolved           │
│  - 回写 GitHub PR Review（发前删旧评论，重发=替换）  │
│  - 逐条行号校验：幻觉行号单独降级，不拖垮整片 inline  │
│  - 兜底：inline 整体失败时回退为纯文本 PR comment    │
└──────────────────────────────────────────────────────┘
```

---

## 架构 & 基础设施

```
┌────────────┐     ┌──────────┐     ┌────────────────┐
│  FastAPI    │────▶│  Redis   │◀────│ Celery Worker  │
│  (Web)     │     │ (Broker  │     │ (LangGraph)    │
│  Port 8000 │     │  +Dedup) │     │                │
└────────────┘     └──────────┘     └───┬───┬────────┘
                                        │   │
                          ┌─────────────┘   └──────────────┐
                   ┌──────▼──────┐  ┌──────▼──────┐  ┌─────▼─────┐
                   │  GitHub API │  │ AI Gateway  │  │ PostgreSQL│
                   │(App + REST) │  │ (自研 Java) │  │ (pgvector)│
                   │+ Check Run  │  └─────────────┘  └───────────┘
                   └─────────────┘
```

**关键架构决策：**

- GitHub App 认证（JWT RS256 → Installation Token），PAT 模式保留为本地开发 fallback
- Check Run API 实现 merge-blocking（advisory / enforced 策略可配）
- 密钥扫描独立于 LLM（pre-graph 旁路），不可被 Agent 覆盖
- 单服务，不拆微服务（实验室规模不需要）
- Celery + Redis broker（异步解耦、去重、重试）
- AI Gateway 复用（自研 Java Spring Cloud Gateway，两个 scenario 路由不同模型）
- PostgreSQL 持久化（review 历史 + 跨 PR 追踪 + 未来 pgvector 语义检索）
- Docker Compose 单机部署（5 个服务）

---

## 技术选型

| 组件 | 技术选择 | 选型理由 |
|------|----------|----------|
| **Agent 框架** | LangGraph StateGraph | 状态图表达 scan→escalate 分支，原生支持 checkpointer 断点续传 |
| **LLM 接口** | langchain-openai ChatOpenAI | 指向自研 AI Gateway 的 OpenAI 兼容 /v1 endpoint |
| **Web 框架** | FastAPI + uvicorn | 异步高性能，Webhook 10s 超时下快速响应 |
| **异步任务** | Celery 5 + Redis | 成熟稳定，支持重试/路由/独立队列扩容 |
| **GitHub 认证** | PyJWT + cryptography | GitHub App JWT RS256 → Installation Token |
| **GitHub 交互** | PyGithub + requests | PyGithub 做 PR 操作，requests 做 Check Run REST API |
| **持久化** | PostgreSQL + SQLAlchemy | review 历史、comments、agent traces |
| **Checkpointer** | langgraph-checkpoint-postgres | Agent 崩溃后断点续传，每个 review 独立 thread_id |
| **LLM 重试** | tenacity | 3 次重试 APITimeoutError / RateLimitError |
| **配置管理** | pydantic-settings | 类型安全的环境变量解析 |
| **日志** | structlog (JSON) | 结构化日志，便于 ELK/Datadog 采集 |

---

## 项目结构

```
app/
├── main.py                     # FastAPI 入口
├── core/                       # 基础设施层
│   ├── config.py               # Pydantic Settings（lru_cache 单例）
│   ├── celery_app.py           # Celery 初始化、任务路由
│   ├── dedup.py                # Redis SET NX 幂等去重
│   └── logging.py              # structlog JSON 日志
├── api/
│   └── webhook.py              # Webhook 接收、HMAC 验签、事件过滤
├── agent/                      # LangGraph Agent 核心
│   ├── graph.py                # StateGraph 构建（7 个节点 + 条件路由）
│   ├── state.py                # ReviewState TypedDict
│   └── prompts.py              # System prompt + deep review + compress prompt
├── services/                   # 业务服务层
│   ├── github.py               # GitHub 双模式认证（App / PAT）+ API 封装
│   ├── check_run.py            # Check Run 生命周期（create → update → conclusion）
│   ├── persistence.py          # PostgreSQL 持久化 + 反馈收集（emoji 👍/👎）
│   ├── reviewer.py             # Review 结果聚合 + GitHub 回写 + 评论 ID 记录
│   └── tools/                  # Agent 工具集（16 个工具，6 个模块）
└── tasks/
    └── review.py               # Celery 任务，串联完整 review 流程

tests/                          # 154 个测试
alembic/                        # 数据库迁移
Dockerfile                      # Multi-stage build（python:3.12-slim）+ entrypoint 自动迁移
docker-compose.yml              # 5 个服务编排
entrypoint.sh                   # 启动前执行 alembic upgrade head
.env.example                    # 所有配置项模板
```

---

## LangGraph Agent 核心设计

### Graph 流程

```
START → scan_call → scan_router
  ├─ has_tool_calls → scan_tools → post_tool_processing → tools_router
  │     ├─ continue       → scan_call（循环）
  │     ├─ compress       → compress_context → scan_call（压缩后继续）
  │     ├─ finish         → parse_result → END
  │     ├─ escalate       → extract_escalation → deep_review → END
  │     └─ budget/loop    → parse_result → END（强制终止）
  └─ no_tool_calls → parse_result → END
```

**7 个节点：** scan_call, scan_tools (ToolNode), post_tool_processing, parse_result, extract_escalation, deep_review, compress_context

### 双模型路由

| Scenario | 模型 | Fallback | 用途 |
|----------|------|----------|------|
| `code-review-scan` | Gemini 3.0 Flash | DeepSeek V4 Flash | ReAct 循环，工具调用 + 上下文收集 |
| `code-review-reason` | Claude Sonnet 4.6 | Gemini 3.5 Flash | 高风险 PR 深度分析（escalate 后触发） |

大部分 PR 全程 Flash（便宜快），只有 Agent 判定 high risk 时才升级到 Sonnet。

### 终止条件

| 条件 | 处理方式 |
|------|----------|
| Agent 调用 `finish_review` | 正常结束，输出 review |
| Agent 调用 `escalate` | 中断 Flash，升级 Sonnet deep review |
| 达到 max_rounds（15 轮） | 强制终止，基于已收集信息输出 review |
| input tokens 超过预算（60K） | 强制终止 |
| 连续 3 次相同工具调用 | 检测到死循环，强制终止 |
| GraphRecursionError | 产出降级结果，不崩溃 |

### Checkpointer & 断点续传

- `PostgresSaver` 持久化 graph state；`thread_id = repo:pr:ref`，每个 review 独立隔离
- `recursion_limit=100` 防止图无限执行；`@lru_cache` 缓存编译后的 graph（进程级单例）

---

## 工具集（16 个）

**P0 — 最小工作集（9 个）：** `read_file`、`search_code`、`find_references`、`get_pr_info`、`get_pr_diff`、`get_pr_changed_files`、`read_repo_rules`、`finish_review`、`escalate`

**P1 — 高价值增强（7 个）：** `find_definition`、`git_log`、`git_blame`、`query_review_history`、`scan_secrets`、`check_test_coverage`、`get_ci_status` / `get_ci_logs`

---

## 审查维度

Agent 不查低级代码问题（Claude Code / linter 已覆盖），聚焦五个维度：

1. **集成风险** — 接口契约破坏、隐式依赖断裂、状态不一致、配置不同步
2. **行为变更** — Prompt 漂移、默认值变更、查询语义变化、错误处理路径改变
3. **安全合规** — 密钥泄露、敏感数据暴露、权限越级
4. **协作盲区** — 并行 PR 冲突、重复实现、违反团队约定、历史坑点重犯
5. **工程健康** — 改了代码没改测试、新增 TODO、依赖风险、Migration 无 rollback

**原则：看到证据才报，看不到就不报。** 每个发现必须通过工具调用验证。

---

## Re-review 流程

新 push 触发 webhook → 从 PostgreSQL 加载上次 review 的 comments → 注入 `prior_comments` + `last_reviewed_sha` 到 Agent state → Agent 带着"上次提了什么问题"进入循环：

- 已修复 → `severity: resolved`，标记 `prior_comment_id`（GitHub 上同步标记）
- 未修复 → 重新提醒
- 改错了 → 新评论

---

## Per-repo 配置

每个仓库可在 `.ai-review/config.yml` 自定义 Agent 行为：

```yaml
ignore_paths:
  - "*.lock"
  - "generated/**"
  - "docs/**"

tech_stack:
  language: python
  framework: fastapi
  testing: pytest

check_policy: advisory   # advisory (默认，不阻断) | enforced (高风险阻断合并)
```

文件不存在或解析失败 → 静默降级为空配置。

---

## GitHub App 认证 & Check Run

### 双模式认证

| 模式 | 触发条件 | 认证方式 | Check Run |
|------|----------|----------|-----------|
| **App 模式** | `GITHUB_APP_ID` 已设置 | JWT RS256 → Installation Token（1h 有效，提前 5 分钟刷新） | 启用 |
| **PAT 模式** | 仅 `GITHUB_APP_TOKEN` 设置 | Personal Access Token 直连 | 跳过 |

### Check Run 结论计算

| 条件 | 结论 |
|------|------|
| 密钥扫描发现泄露 | `failure`（无条件一票否决） |
| `check_policy=enforced` + high risk | `failure`（阻断合并） |
| `check_policy=enforced` + medium risk | `neutral` |
| `check_policy=enforced` + low risk | `success` |
| `check_policy=advisory`（默认） | `neutral`（永不阻断） |

`create_check_run` 用 `external_id = bot4bread:{head_sha}` 先查后建，at-least-once 重试下复用而非重复创建。注解映射：error→failure, warning→warning, suggestion→notice（上限 50 条/次）。

### 独立密钥扫描（安全旁路）

`run_secret_scan()` 在 `graph.invoke()` 之前独立执行，结果注入 state 供 LLM 参考，但结论直接决定 Check Run——LLM 无法覆盖 `secret_failed=True → failure` 的逻辑。

### 开发者反馈收集

每次 review 前自动收集上次 bot 评论的 emoji：👎 → `false_positive`，👍 → `helpful`（两者都有时 👎 优先）。存入 `review_comments.feedback`，通过 `query_review_history` 工具暴露给 Agent。

---

## 可靠性设计

| 机制 | 实现 |
|------|------|
| **Webhook 去重** | Redis SET NX + TTL 3600s（按 X-GitHub-Delivery） |
| **Celery 重试** | max_retries=3, acks_late=True, task_reject_on_worker_lost=True |
| **副作用幂等** | at-least-once 下任务可能重跑，三处副作用全部幂等：Check Run 按 `external_id` 复用、PR 评论发前删旧（隐藏标记 `<!-- bot4bread:ai-review -->`）实现替换而非堆叠、`save_review` 按 `(repo, pr, sha)` 先删后插 + 唯一约束 |
| **commit 钉定** | head_sha 从 webhook payload 透传，重试 review 的永远是触发那次的 commit，而非可能更新的 live HEAD |
| **异常分类重试** | `_is_retryable()`：限流/5xx/网络超时才重试；4xx（404、422）直接死信终态 |
| **死信终态** | 重试耗尽或不可重试 → `review_dead_lettered` 日志 + Check Run 置 failure，不静默消失 |
| **全链路关联** | delivery_id 从 webhook 透传进任务并 `log.bind`，单 ID 串起 webhook → task → 回写 |
| **死循环检测** | 连续 3 次相同工具调用指纹 → 强制终止 |
| **上下文爆炸** | 多轮压缩（round 5, 10, 15...）+ 工具返回长度限制 |
| **Graph 递归** | recursion_limit=100 + GraphRecursionError 捕获 → 降级结果 |
| **Checkpointer** | PostgresSaver 断点续传，thread_id 隔离 |
| **行号幻觉防护** | 逐条比对 diff 可标注行号，幻觉行号单独降级为文本，不拖垮整片 inline |
| **评论降级** | PR Review inline 整体失败 → 回退纯文本 PR comment |

---

## 部署与运行

### Docker Compose（推荐，5 个服务）

| 服务 | 镜像 | 说明 |
|------|------|------|
| review-agent | python:3.12-slim (multi-stage) | FastAPI + uvicorn |
| celery-worker | 同上 | Celery Worker |
| ai-gateway | ai-api-gateway:latest | 自研 AI Gateway (Java) |
| postgres | pgvector/pgvector:pg16 | Review 持久化 + Checkpointer |
| redis | redis:7-alpine | Broker + 去重 |

```bash
cp .env.example .env             # 填写真实值
# 确保 private-key.pem 在项目根目录（App 模式）
docker compose up -d             # 启动所有服务
```

`entrypoint.sh` 在每个容器启动时先执行 `alembic upgrade head` 再启动应用；`private-key.pem` 通过 volume 只读挂载进容器。

### 本地开发（不用 Docker）

前置依赖：Python 3.12+、Redis、PostgreSQL。

```bash
pip install -r requirements.txt
cp .env.example .env             # 编辑配置
alembic upgrade head             # 建表

# 终端 1：FastAPI
uvicorn app.main:app --reload --port 8000

# 终端 2：Celery Worker（IO 密集，建议 gevent 池）
celery -A app.core.celery_app worker --pool=gevent --concurrency=20 --queues=reviews --loglevel=info
```

本地暴露 webhook 端口可用 [ngrok](https://ngrok.com)：`ngrok http 8000`。

### 配置 GitHub Webhook

GitHub 仓库 / 组织 → Settings → Webhooks → Add webhook：

- **Payload URL**：`https://your-domain.com/api/v1/webhook/github`
- **Content type**：`application/json`
- **Secret**：填写 `GITHUB_WEBHOOK_SECRET` 的值
- **Events**：勾选 `Pull requests`

### 关键环境变量

| 变量 | 说明 |
|------|------|
| `GITHUB_WEBHOOK_SECRET` | Webhook HMAC 签名密钥 |
| `GITHUB_APP_ID` | GitHub App ID（设置后启用 App 模式） |
| `GITHUB_APP_PRIVATE_KEY_PATH` | App 私钥路径（默认 `./private-key.pem`） |
| `GITHUB_APP_INSTALLATION_ID` | App 安装 ID |
| `GITHUB_APP_TOKEN` | PAT fallback（仅 APP_ID 未设置时生效） |
| `DATABASE_URL` | PostgreSQL 连接串 |
| `REDIS_URL` | Redis 连接地址 |
| `AI_GATEWAY_URL` | AI Gateway 地址 |
| `AI_GATEWAY_KEY` | Gateway Bearer Token |
| `SCAN_SCENARIO` | scan 模型 scenario（默认 `code-review-scan`） |
| `REASON_SCENARIO` | reason 模型 scenario（默认 `code-review-reason`） |
| `MAX_ROUNDS` | Agent 最大循环轮次（默认 15） |
| `MAX_INPUT_TOKENS` | 单次 review token 上限（默认 60000） |
| `COMPRESS_AT_ROUND` | 上下文压缩间隔（默认 5） |

---

## 测试

```bash
python -m pytest tests -q        # 154 个测试，mock 掉 LLM 与 GitHub
```

测试覆盖图的状态流转与编排逻辑（终止条件、re-review、副作用幂等、行号降级、重试分类），而非 LLM 输出内容本身。

---

## Roadmap

1. **P2 工具**：check_open_prs_overlap（并行 PR 冲突检测）、scan_todos、check_migration_files
2. **issue_patterns 自动统计**：同类问题 ≥ 3 次时自动沉淀到 `.ai-review/known-issues/`
3. **pgvector 语义检索**：历史 review 语义相似查询 + 基于反馈的误报抑制
4. **finish_review 输出强校验**：Pydantic schema（行号/文件存在性校验已实现）
5. **同 PR 在途取消**：`repo:pr` 维度分布式锁 + revoke 在途 task

---

> 面试备战材料见 [`docs/INTERVIEW_PREP.md`](docs/INTERVIEW_PREP.md)。
