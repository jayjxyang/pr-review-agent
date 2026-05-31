# PR Review Agent — 项目全景文档

> 面试备忘：从业务背景、架构设计、技术选型到核心实现，一文掌握项目全貌。

---

## 1. 项目定位 & 解决的问题

**一句话描述：** 基于 LangGraph ReAct Agent 的 GitHub PR 自动 Code Review 守门员。

**定位：**
> CC 是作者的副驾驶，PR Review Agent 是团队的守门员。

不是帮作者"写得更好"，而是帮团队"拦住风险"和"看见全局"。

**痛点：**
- 团队大部分人使用 Claude Code 等 AI 辅助编码，低级代码问题已大幅减少
- 人工 Code Review 容易遗漏跨模块集成风险、行为变更、安全合规问题
- 不同 Reviewer 标准不统一，PR 可能长时间无人 Review

**解决方案：**
- GitHub App 认证 + Check Run API → 可阻断合并（不只是 comment）
- GitHub Webhook 触发 → Celery 异步派发 → LangGraph Agent 自主循环
- Agent 拥有 16 个工具，按需读源码、查历史、跑检查，自己决定"看什么、看多少"
- 风险判定驱动双模型路由：低/中风险用 Flash 快速出 review，高风险升级 Sonnet 深度分析
- 密钥扫描独立于 LLM 循环，结果不可被 LLM 覆盖（安全旁路）
- 支持 re-review：新 push 后增量 review，自动标记已修复的问题
- 开发者 emoji 反馈（👍/👎）→ 自动回收，未来可做误报抑制

---

## 2. 端到端流程

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
│  - 立即返回 202 Accepted                             │
│  - 投递 Celery 异步任务                              │
└───────────────┬──────────────────────────────────────┘
                │
                ▼
┌─ Phase 2：Pre-graph 准备 ───────────────────────────┐
│  - collect_feedback() — 收集上次评论的 emoji 反馈     │
│  - create_check_run() — 创建 in_progress Check Run   │
│  - run_secret_scan() — 独立密钥扫描（LLM 不可覆盖）  │
│  - 加载 re-review 上下文（prior_comments）            │
└───────────────┬──────────────────────────────────────┘
                │
                ▼
┌─ Phase 3：LangGraph Agent 循环 ─────────────────────┐
│                                                      │
│  scan_call → scan_tools → post_tool_processing       │
│       ↑           │                                  │
│       └───────────┘  ← tools_router 条件路由         │
│                                                      │
│  终止条件：                                           │
│  ├─ finish_review → parse_result → 输出 review       │
│  ├─ escalate → deep_review（Sonnet）→ 输出 review    │
│  ├─ 达到 max_rounds / token 预算                      │
│  └─ 检测到死循环（连续 3 次相同工具调用）              │
│                                                      │
│  第 5/10/15 轮触发上下文压缩 → 继续循环               │
└───────────────┬──────────────────────────────────────┘
                │
                ▼
┌─ Phase 4：结果处理 ─────────────────────────────────┐
│  - compute_conclusion() — 综合安全扫描 + 风险等级     │
│  - update_check_run() — 写 Check Run 结论 + 注解     │
│  - 持久化到 PostgreSQL（review + comments + traces） │
│  - Re-review 时标记旧 comments 为 resolved           │
│  - 回写 GitHub PR Review（inline comments）          │
│  - update_github_comment_ids() — 记录评论 ID 供反馈   │
│  - 降级：行号无效时回退为纯文本 PR comment            │
└──────────────────────────────────────────────────────┘
```

---

## 3. 架构 & 基础设施

```
┌────────────┐     ┌──────────┐     ┌────────────────┐
│  FastAPI    │────▶│  Redis   │◀────│ Celery Worker  │
│  (Web)     │     │ (Broker  │     │ (LangGraph)    │
│  Port 8000 │     │  +Dedup) │     │                │
└────────────┘     └──────────┘     └───┬───┬────────┘
                                        │   │
                          ┌─────────────┘   └──────────────┐
                          │                                 │
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
- Docker Compose 单机部署（5 个服务：review-agent, celery-worker, ai-gateway, postgres, redis）

---

## 4. 技术选型

| 组件 | 技术选择 | 选型理由 |
|------|----------|----------|
| **Agent 框架** | LangGraph StateGraph | 状态图表达 scan→escalate 分支逻辑，原生支持 checkpointer 断点续传 |
| **LLM 接口** | langchain-openai ChatOpenAI | 指向自研 AI Gateway 的 OpenAI 兼容 /v1 endpoint |
| **Web 框架** | FastAPI + uvicorn | 异步高性能，Webhook 10s 超时限制下快速响应 |
| **异步任务** | Celery 5 + Redis | 成熟稳定，支持重试/路由/独立队列扩容 |
| **GitHub 认证** | PyJWT + cryptography | GitHub App JWT RS256 签名 → Installation Token |
| **GitHub 交互** | PyGithub + requests | PyGithub 做 PR 操作，requests 做 Check Run REST API |
| **持久化** | PostgreSQL + SQLAlchemy | review 历史、comments、agent traces |
| **Checkpointer** | langgraph-checkpoint-postgres | Agent 崩溃后断点续传，每个 review 独立 thread_id |
| **LLM 重试** | tenacity | 3 次重试 APITimeoutError / RateLimitError |
| **配置管理** | pydantic-settings | 类型安全的环境变量解析 |
| **日志** | structlog (JSON) | 结构化日志，便于 ELK/Datadog 采集 |

---

## 5. 项目结构

```
app/
├── main.py                     # FastAPI 入口
├── core/                       # 基础设施层
│   ├── config.py               # Pydantic Settings（lru_cache 单例）
│   ├── celery_app.py           # Celery 初始化、任务路由
│   ├── dedup.py                # Redis SET NX 幂等去重
│   └── logging.py              # structlog JSON 日志
├── api/                        # 接入层
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
│       ├── code_read.py        # read_file, search_code, find_references, find_definition
│       ├── pr_context.py       # get_pr_info, get_pr_changed_files, get_pr_diff
│       ├── git_history.py      # git_log, git_blame
│       ├── knowledge.py        # read_repo_rules, query_review_history
│       ├── quality.py          # scan_secrets, check_test_coverage, get_ci_status, get_ci_logs
│       └── control.py          # finish_review, escalate
└── tasks/                      # 任务编排层
    └── review.py               # Celery 任务，串联完整 review 流程

tests/                          # 144 个测试
Dockerfile                      # Multi-stage build（python:3.12-slim）+ entrypoint 自动迁移
docker-compose.yml              # 5 个服务编排
entrypoint.sh                   # 启动前执行 alembic upgrade head
.dockerignore                   # 排除 .git、tests、.pem 等
.env.example                    # 所有配置项模板
```

---

## 6. LangGraph Agent 核心设计

### 6.1 Graph 流程

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

### 6.2 ReviewState

```python
class ReviewState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    repo: str
    pr_number: int
    ref: str
    risk_level: str           # low / medium / high
    summary: str
    comments: list[dict]      # [{filename, line, severity, comment}]
    escalated: bool
    escalate_reason: str
    round_count: int          # 当前轮次
    total_input_tokens: int   # 累计 input tokens
    tool_call_history: list[str]  # 工具调用指纹（死循环检测）
    traces: list[dict]        # agent 推理轨迹
    compress_count: int       # 已压缩次数（触发 at round 5, 10, 15...）
    prior_comments: list[dict]    # re-review: 上次的 comments
    last_reviewed_sha: str        # re-review: 上次 review 的 commit SHA
    repo_config: dict             # per-repo .ai-review/config.yml
    secret_findings: list[dict]   # pre-graph 独立密钥扫描结果
```

### 6.3 双模型路由

| Scenario | 模型 | Fallback | 用途 |
|----------|------|----------|------|
| `code-review-scan` | Gemini 3.0 Flash | DeepSeek V4 Flash | ReAct 循环，工具调用 + 上下文收集 |
| `code-review-reason` | Claude Sonnet 4.6 | Gemini 3.5 Flash | 高风险 PR 深度分析（escalate 后触发） |

大部分 PR 全程 Flash（便宜快），只有 Agent 判定 high risk 时才升级到 Sonnet。

### 6.4 终止条件

| 条件 | 处理方式 |
|------|----------|
| Agent 调用 `finish_review` | 正常结束，输出 review |
| Agent 调用 `escalate` | 中断 Flash，升级 Sonnet deep review |
| 达到 max_rounds（15 轮） | 强制终止，基于已收集信息输出 review |
| input tokens 超过预算（60K） | 强制终止 |
| 连续 3 次相同工具调用 | 检测到死循环，强制终止 |
| GraphRecursionError | 产出降级结果，不崩溃 |

### 6.5 上下文压缩

循环第 5 轮时触发：LLM 将前几轮的工具结果压缩为结构化摘要，替换原始消息。支持多轮压缩（第 5、10、15 轮...），通过 `compress_count` 追踪。

### 6.6 Checkpointer & 断点续传

- `PostgresSaver` 持久化 graph state 到 PostgreSQL
- `thread_id = f"{repo}:{pr}:{ref}"`，每个 review 独立隔离
- `recursion_limit=100` 防止图无限执行
- `@lru_cache(maxsize=1)` 缓存编译后的 graph（进程级单例）

---

## 7. 工具集（16 个）

### P0 — Agent 最小工作集（9 个）

| 工具 | 功能 | 底层实现 |
|------|------|----------|
| `read_file` | 读任意文件，支持范围 | GitHub Contents API |
| `search_code` | 全仓库内容搜索 | GitHub Search API |
| `find_references` | 找函数/类的所有调用方 | search_code + 正则 |
| `get_pr_info` | PR 标题、描述、作者、标签 | GitHub PR API |
| `get_pr_diff` | 完整或指定文件的 diff | GitHub PR API |
| `get_pr_changed_files` | 变更文件列表 + 增删行数 | GitHub PR API |
| `read_repo_rules` | 读 `.ai-review/rules/` | GitHub Contents API |
| `finish_review` | 结束循环，输出最终结果 | 控制信号 |
| `escalate` | 标记 high risk，升级到 Sonnet | 控制信号 |

### P1 — 高价值增强（7 个）

| 工具 | 功能 |
|------|------|
| `find_definition` | 找符号的定义位置 |
| `git_log` | 最近提交记录 |
| `git_blame` | 代码归属和变更原因（GraphQL） |
| `query_review_history` | 查历史 review 中的相关问题 |
| `scan_secrets` | 扫描 diff 中的疑似密钥 |
| `check_test_coverage` | 检查函数是否被测试引用 |
| `get_ci_status` / `get_ci_logs` | CI check 状态 + 失败日志 |

---

## 8. 审查维度

Agent 不查低级代码问题（CC / linter 已覆盖），聚焦五个维度：

1. **集成风险** — 接口契约破坏、隐式依赖断裂、状态不一致、配置不同步
2. **行为变更** — Prompt 漂移、默认值变更、查询语义变化、错误处理路径改变
3. **安全合规** — 密钥泄露、敏感数据暴露、权限越级
4. **协作盲区** — 并行 PR 冲突、重复实现、违反团队约定、历史坑点重犯
5. **工程健康** — 改了代码没改测试、新增 TODO、依赖风险、Migration 无 rollback

**原则：看到证据才报，看不到就不报。** 每个发现必须通过工具调用验证。

---

## 9. Re-review 流程

```
新 push 触发 webhook
    │
    ├→ 查 PostgreSQL：上次 review 的 comments
    ├→ 注入 prior_comments + last_reviewed_sha 到 Agent state
    │
    ▼
Agent 循环（带着"上次提了什么问题" + "新的改动"）
    │
    ▼
输出：新 comments + 旧 comments 状态更新
    ├─ 已修复 → severity: "resolved"，标记 prior_comment_id
    ├─ 未修复 → 重新提醒
    └─ 改错了 → 新评论
```

---

## 10. Per-repo 配置

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

- `ignore_paths`：注入到 system prompt，Agent 跳过这些文件
- `tech_stack`：注入到 system prompt，Agent 了解项目技术栈
- `check_policy`：控制 Check Run 结论逻辑（见下方 Check Run 章节）
- 文件不存在或解析失败 → 静默降级为空配置

---

## 11. GitHub App 认证 & Check Run

### 11.1 双模式认证

| 模式 | 触发条件 | 认证方式 | Check Run |
|------|----------|----------|-----------|
| **App 模式** | `GITHUB_APP_ID` 已设置 | JWT RS256 → Installation Token（1h 有效，提前 5 分钟刷新） | 启用 |
| **PAT 模式** | 仅 `GITHUB_APP_TOKEN` 设置 | Personal Access Token 直连 | 跳过 |

App 模式下：`PyJWT` + `cryptography` 签发 10 分钟有效 JWT，换取 Installation Token，内存缓存。

### 11.2 Check Run 生命周期

```
create_check_run(status="in_progress")
    → Agent 循环
    → compute_conclusion(secret_failed, risk_level, check_policy)
    → update_check_run(conclusion, annotations)
```

**结论计算逻辑：**

| 条件 | 结论 |
|------|------|
| 密钥扫描发现泄露 | `failure`（无条件一票否决） |
| `check_policy=enforced` + high risk | `failure`（阻断合并） |
| `check_policy=enforced` + medium risk | `neutral` |
| `check_policy=enforced` + low risk | `success` |
| `check_policy=advisory`（默认） | `neutral`（永不阻断） |

**注解映射：** error→failure, warning→warning, suggestion→notice（上限 50 条/次，GitHub API 限制）

### 11.3 独立密钥扫描（安全旁路）

```
旧方案：scan_secrets 是 LLM 工具之一 → LLM 可以不调用、忽略结果
新方案：run_secret_scan() 在 graph.invoke() 之前独立执行
        → 结果注入 state 供 LLM 参考，但结论直接决定 Check Run
        → LLM 无法覆盖 secret_failed=True → failure 的逻辑
```

### 11.4 开发者反馈收集

每次 review 开始前，自动收集上次 bot 评论的 emoji 反应：

| 反应 | 含义 | 存储 |
|------|------|------|
| 👎 | 误报 (false_positive) | `review_comments.feedback = "false_positive"` |
| 👍 | 有用 (helpful) | `review_comments.feedback = "helpful"` |
| 两者都有 | 👎 优先 | conservative — 以误报为准 |

反馈数据通过 `query_review_history` 工具暴露给 LLM，帮助 Agent 了解"这类发现之前被标记为误报"。

---

## 12. 可靠性设计

| 机制 | 实现 |
|------|------|
| **Webhook 去重** | Redis SET NX + TTL 3600s |
| **Celery 重试** | max_retries=3, acks_late=True, task_reject_on_worker_lost=True |
| **LLM 重试** | tenacity @retry 3 次（APITimeoutError, RateLimitError） |
| **死循环检测** | 连续 3 次相同工具调用指纹 → 强制终止 |
| **上下文爆炸** | 多轮压缩（round 5, 10, 15...）+ 工具返回长度限制 |
| **Graph 递归** | recursion_limit=100 + GraphRecursionError 捕获 → 降级结果 |
| **Checkpointer** | PostgresSaver 断点续传，thread_id 隔离 |
| **安全旁路** | run_secret_scan() 独立于 LLM，密钥泄露 → 无条件 failure |
| **Check Run 降级** | graph 异常时仍尝试更新 Check Run 为 failure + 错误摘要 |
| **评论降级** | PR Review inline 失败 → 回退纯文本 PR comment |

---

## 13. 部署

### Docker Compose（5 个服务）

| 服务 | 镜像 | 说明 |
|------|------|------|
| review-agent | python:3.12-slim (multi-stage) | FastAPI + uvicorn，entrypoint 自动迁移 |
| celery-worker | 同上 | Celery Worker，entrypoint 自动迁移 |
| ai-gateway | ai-api-gateway:latest | 自研 AI Gateway (Java) |
| postgres | pgvector/pgvector:pg16 | Review 持久化 + Checkpointer |
| redis | redis:7-alpine | Broker + 去重 |

### 启动流程

```bash
cp .env.example .env             # 填写真实值
# 确保 private-key.pem 在项目根目录
docker compose up -d             # 启动所有服务
```

`entrypoint.sh` 在每个容器启动时先执行 `alembic upgrade head`，再启动应用。`private-key.pem` 通过 volume 只读挂载进容器。

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
| `SCAN_SCENARIO` | scan 模型 scenario (code-review-scan) |
| `REASON_SCENARIO` | reason 模型 scenario (code-review-reason) |
| `MAX_ROUNDS` | Agent 最大循环轮次 (默认 15) |
| `MAX_INPUT_TOKENS` | 单次 review token 上限 (默认 60000) |
| `COMPRESS_AT_ROUND` | 上下文压缩间隔 (默认 5) |

---

## 14. 面试高频问题

### Q: 为什么用 LangGraph 而不是普通 pipeline？
**A:** 旧版是固定 pipeline（取 diff → 分块 → 单次 LLM → 发评论），只看 diff 不读源码、不了解上下文、不记历史。LangGraph StateGraph 让 Agent 自主决定工具调用顺序和深度，支持条件分支（escalate 到更强模型）、循环（多轮工具调用）、断点续传（checkpointer）。岗位要求熟悉 LangChain/LangGraph。

### Q: 双模型路由怎么工作？
**A:** 渐进式升级。大部分 PR 全程 Gemini Flash（快且便宜）。Agent 在扫描过程中如果判定 high risk（改动影响多模块、破坏性变更等），调用 `escalate` 工具，框架自动将收集到的上下文压缩交接给 Claude Sonnet 做深度分析。风险判定是扫描的副产品，零额外成本。

### Q: 如何防止 Agent 无限循环？
**A:** 五层防护：① max_rounds 轮次上限 ② max_input_tokens token 预算 ③ 死循环检测（连续 3 次相同工具调用） ④ recursion_limit=100 ⑤ GraphRecursionError 捕获产出降级结果。

### Q: 高并发下不同 PR 的 Agent 状态如何隔离？
**A:** thread_id = `repo:pr:ref`。每个 PR 的每次 review（不同 commit SHA）有独立的 checkpointer 线程。Celery Worker 并发执行不同 PR 的 review，graph state 通过 PostgresSaver 持久化，互不干扰。

### Q: Re-review 怎么做增量？
**A:** 从 PostgreSQL 加载上次 review 的 comments，注入到 Agent 的 system prompt。Agent 带着"上次提了什么问题"进入循环，对每个旧 comment 判断：已修复 → resolved，未修复 → 重新提醒，改错了 → 新评论。resolved 的 comments 在 GitHub 上也会被标记。

### Q: 上下文爆炸怎么处理？
**A:** 三层控制：① 工具返回长度限制（read_file 300 行、search_code 20 条、单文件 diff 500 行截断） ② Agent 自主决定"看什么"（先 get_pr_changed_files 看列表，再逐文件 get_pr_diff） ③ 多轮压缩（第 5 轮开始，LLM 将早期工具结果压缩为结构化摘要）。

### Q: 为什么用 Celery 异步？
**A:** GitHub Webhook 有 10 秒超时限制。Agent 循环可能跑十几轮、几十秒到几分钟。FastAPI 在 < 100ms 内返回 202 Accepted，真正的 review 交给 Celery Worker 后台完成。

### Q: 和 AI Gateway 的关系？
**A:** AI Gateway 是独立的自研项目（Java Spring Cloud Gateway），提供 OpenAI 兼容 /v1 endpoint + 多 LLM provider 路由 + 按 tenant 限流。PR Review Agent 通过 langchain-openai ChatOpenAI 指向 Gateway，scenario alias 做模型路由。两个项目合在一起讲一个完整故事：网关设计 → LangGraph Agent → 实际应用。

### Q: 为什么用 GitHub App 而不是 PAT？
**A:** 三个原因：① PAT 绑定个人账号，人员离职 token 失效；App 绑定组织，与人无关 ② PAT 无法创建 Check Run，只能发 comment（advisory only）；App 可以通过 Check Run API 阻断合并 ③ App 有更高 Rate Limit（5000→15000/h）和更细粒度权限控制。

### Q: Check Run 和 PR Review 的区别？
**A:** PR Review 是"评论"——人能看到但不阻断流程。Check Run 是 CI 状态——在 PR 的 Checks tab 显示为 ✅/❌，可以配合 branch protection rule 阻断合并。Bot4Bread 两个都发：Check Run 做 gate（结论由策略引擎决定），PR Review 做 inline comment（内容由 Agent 决定）。

### Q: 为什么密钥扫描要从 LLM 工具中独立出来？
**A:** 安全关键路径不应该依赖 LLM 的判断力。作为 LLM 工具时：① LLM 可能不调用 ② 调用了也可能忽略结果 ③ 可能被 prompt injection 绕过。独立后：`run_secret_scan()` 在 Agent 循环之前执行，结果直接控制 Check Run 结论（`secret_failed=True` → `failure`），LLM 只能在 summary 中引用结果，不能覆盖决策。

### Q: 反馈收集怎么工作？
**A:** 零摩擦方案。开发者对 bot 的 inline comment 加 👍 或 👎 emoji，下次 review 触发时自动收集：👎 → false_positive，👍 → helpful。数据存入 `review_comments.feedback`，通过 `query_review_history` 工具暴露给 Agent。当前是信息展示，未来可做 pgvector 语义检索 + 自动误报抑制。

---

## 15. 未来方向

1. **P2 工具**：check_open_prs_overlap（并行 PR 冲突检测）、scan_todos、check_migration_files 等
2. **issue_patterns 自动统计**：同类问题 ≥ 3 次时自动沉淀到 `.ai-review/known-issues/`
3. **pgvector 语义检索**：历史 review 的语义相似性查询 + 基于反馈的误报抑制
4. **finish_review 输出校验**：Pydantic schema 验证 + 行号/文件存在性校验
