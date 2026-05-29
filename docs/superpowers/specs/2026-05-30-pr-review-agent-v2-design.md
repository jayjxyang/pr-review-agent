# PR Review Agent V2 — 从 Pipeline 升级为 Agent

## 背景

当前 pr-review-agent 是一个固定 pipeline：webhook → 取 diff → 分 chunk → 单次 LLM 调用 → 发评论。它只看 diff，不读源代码，不了解项目上下文，不记住历史。

团队大部分人使用 Claude Code 等 AI 辅助编码，低级代码问题已大幅减少。当前 pipeline 的 review 价值有限。

## 定位

> CC 是作者的副驾驶，PR Review Agent 是团队的守门员。

不是帮作者"写得更好"，而是帮团队"拦住风险"和"看见全局"。

## 应用场景

实验室多组并行开发 AI Agent 项目，统一使用实验室 AI Gateway，review agent 拥有所有组代码仓库权限，PR 提交后自动 review。

---

## 架构

### 整体流程

```
GitHub Webhook (PR opened/synchronized)
       │
       ▼
┌─ Review Agent Service ──────────────────────────────┐
│                                                      │
│  1. Webhook Handler（快速响应，Celery 异步派发）      │
│       │                                              │
│  2. Context Collector（Gemini 3.0 Flash）             │
│       │  ← 32 个工具，按需获取源代码/上下文           │
│       │  → 输出：上下文摘要 + risk_level              │
│       │                                              │
│  3. Risk Router                                      │
│       ├─ low/medium → Gemini 3.0 Flash 直接出 review │
│       └─ high → Claude Sonnet 4.6 深度 review        │
│                                                      │
│  4. Review Publisher → GitHub PR Review              │
│  5. Knowledge Writer → PG + Repo 文件                │
└──────────────────────────────────────────────────────┘
       │
       ▼
┌─ 基础设施 ─────────────┐
│  AI Gateway (已有)      │
│  PostgreSQL (新增)      │
│  Redis (已有)           │
│  Celery (已有)          │
└─────────────────────────┘
```

### 关键架构决策

- 单服务，不拆微服务（实验室规模不需要）
- Celery + Redis broker 保留（异步解耦、去重、重试）
- Gateway 复用，新增两个 scenario 路由不同模型
- PostgreSQL 新增（review 历史 + 跨 PR 统计 + 未来 pgvector）
- Docker Compose 单机部署

### 保留的现有能力

| 能力 | 说明 |
|---|---|
| Celery 异步执行 | webhook 快速响应，review 后台执行 |
| Gateway 限流 | 按 tenant（各组）独立控额度 |
| Webhook 去重 | 同一 PR 短时间多次 push 不重复 review |
| Retry 机制 | LLM 调用失败自动重试 |

---

## 模型策略

### 双层模型路由

| Scenario | 模型 | Fallback | Cost Weight | 用途 |
|---|---|---|---|---|
| `code-review-scan` | Gemini 3.0 Flash | DeepSeek V4 Flash | 1 | 工具调用循环，收集上下文 |
| `code-review-reason` | Claude Sonnet 4.6 | Gemini 3.5 Flash | 5 | 高风险 PR 深度分析 |

### 渐进式升级策略

大部分 PR 全程 Gemini 3.0 Flash（便宜快），只有 agent 在扫描过程中判定 high risk 时才升级到 Claude Sonnet 4.6。风险判定是扫描阶段的副产品，零额外成本。

### 风险判定原则

不硬编码具体场景，给模型判断维度：

- 改动是否影响多个模块或全局行为（配置、依赖、共享模块）
- 改动是否是破坏性的（删除、重命名、接口变更）
- 改动是否难以通过测试覆盖（prompt 变更、行为变化、竞态条件）
- 改动文件是否属于项目核心路径

满足 2 个以上 → high，满足 1 个 → medium，都不满足 → low。

### 成本预算

| 指标 | 值 |
|---|---|
| 单个 PR 平均消耗 | ~40K-60K tokens |
| 单个 PR 上限 | 80K tokens |
| 每日 tenant 额度 | 1,500K tokens |
| bucket capacity | 1,500,000 |
| refill rate | 17.0 tokens/sec |
| 单 PR 成本（大部分 low/medium） | ~$0.01 |
| 单 PR 成本（high risk，触发 Sonnet） | ~$0.085 |
| 每天 20 个 PR 预估 | ~$1-2/天 |

---

## Agent 工具集

### 代码阅读（3 个）

| 工具 | 参数 | 功能 |
|---|---|---|
| `read_file` | `path`, `start_line?`, `end_line?` | 读任意文件，支持范围读取 |
| `list_directory` | `path`, `recursive?` | 查看目录结构 |
| `read_file_summary` | `path` | 返回文件的函数/类签名列表（不含实现） |

### 代码搜索（4 个）

| 工具 | 参数 | 功能 |
|---|---|---|
| `search_code` | `query`, `path_filter?`, `regex?` | 全仓库内容搜索（grep） |
| `find_references` | `symbol`, `path_filter?` | 找函数/类/变量的所有调用方 |
| `find_definition` | `symbol` | 找符号的定义位置 |
| `search_files` | `pattern` | 按文件名模式搜索 |

### Git 历史（3 个）

| 工具 | 参数 | 功能 |
|---|---|---|
| `git_log` | `path?`, `limit?` | 最近提交记录 |
| `git_blame` | `path`, `start_line`, `end_line` | 代码归属和变更原因 |
| `git_diff_between` | `base_ref`, `head_ref`, `path?` | 两个 ref 之间的 diff |

### PR 上下文（5 个）

| 工具 | 参数 | 功能 |
|---|---|---|
| `get_pr_info` | — | PR 标题、描述、作者、标签、关联 issue |
| `get_pr_diff` | `file_path?` | 完整 diff 或指定文件的 diff |
| `get_pr_comments` | — | PR 上已有的评论 |
| `get_pr_changed_files` | — | 变更文件列表 + 增删行数 |
| `get_issue_detail` | `issue_number` | 关联 issue 的完整内容 |

### 依赖分析（2 个）

| 工具 | 参数 | 功能 |
|---|---|---|
| `read_dependencies` | — | 读 package.json / requirements.txt / pom.xml |
| `check_import_graph` | `module_path` | 模块的 import 关系 |

### 项目知识（3 个）

| 工具 | 参数 | 功能 |
|---|---|---|
| `read_repo_rules` | — | 读 `.ai-review/rules/` 下的规则文件 |
| `query_review_history` | `file_path?`, `module?`, `keyword?` | 查历史 review 中的相关问题 |
| `query_common_issues` | `repo_id` | 该仓库高频问题模式 |

### 质量检查（8 个）

| 工具 | 参数 | 功能 |
|---|---|---|
| `find_related_tests` | `source_path` | 找对应的测试文件 |
| `check_test_coverage` | `source_path`, `function_name` | 检查函数是否被测试引用 |
| `check_config_consistency` | — | 新增环境变量是否同步到 .env.example / docker-compose |
| `scan_secrets` | — | 扫描 diff 中的疑似密钥 |
| `scan_todos` | — | 扫描 diff 中新增的 TODO/FIXME |
| `check_migration_files` | — | 检测 DB migration 是否有 rollback |
| `get_ci_status` | — | 获取 PR 的 CI check 状态（pass/fail/pending） |
| `get_ci_logs` | `check_name` | CI 失败时拉取错误日志 |

### 协作感知（2 个）

| 工具 | 参数 | 功能 |
|---|---|---|
| `check_open_prs_overlap` | — | 其他 open PR 是否改了相同文件 |
| `check_downstream_repos` | `module_path` | 共享模块的下游依赖仓库 |

### 控制（2 个）

| 工具 | 参数 | 功能 |
|---|---|---|
| `finish_review` | `risk_level`, `summary`, `comments[]` | 结束循环，输出最终结果 |
| `escalate` | `reason` | 标记 high risk，升级到 Sonnet |

### 工具实现方式

大部分工具底层是 GitHub API 调用：

| 工具 | 底层实现 |
|---|---|
| `read_file` | `GET /repos/{owner}/{repo}/contents/{path}?ref={branch}` |
| `search_code` | `GET /search/code?q={query}+repo:{repo}` |
| `find_references` | `search_code` + 正则过滤 |
| `find_definition` | `search_code` + 正则 `(def\|class\|function)\s+{symbol}` |
| `git_log` | `GET /repos/{owner}/{repo}/commits?path={path}` |
| `git_blame` | GraphQL API |
| `read_file_summary` | `read_file` + 正则提取签名（V2 可接 tree-sitter） |
| `check_import_graph` | `search_code` + import 语句解析 |
| `query_review_history` | PostgreSQL 查询 |
| `scan_secrets` | 正则匹配高熵字符串 + 已知 pattern |

### 工具优先级

| 优先级 | 工具 | 理由 |
|---|---|---|
| P0 | read_file, search_code, find_references, get_pr_info, get_pr_diff, get_pr_changed_files, read_repo_rules, finish_review, escalate | agent 能工作的最小集 |
| P1 | git_blame, git_log, find_definition, query_review_history, scan_secrets, check_test_coverage, get_ci_status, get_ci_logs | 明显提升 review 质量 |
| P2 | check_open_prs_overlap, check_downstream_repos, check_config_consistency, scan_todos, check_migration_files | 协作场景需要时再加 |

---

## 审查维度

Agent 不查低级代码问题（CC / linter 已覆盖），聚焦以下五个维度：

### 1. 集成风险 — "单独看没问题，合在一起出事"

- 接口契约破坏（改了函数签名/返回值，调用方还在用旧的）
- 隐式依赖断裂（删了"看起来没用"的字段，下游通过动态方式在用）
- 状态不一致（两个模块对同一份数据的处理假设不同）
- 配置不同步（代码加了新环境变量，部署配置没跟上）

### 2. 行为变更 — "能跑，但行为不是你想的"

- Prompt 行为漂移（改了 system prompt 导致 agent 行为变化）
- 默认值变更（timeout 从 30s 改成 5s，没通知依赖方）
- 查询语义变化（include_deleted=True 导致展示了不该展示的数据）
- 错误处理路径改变（把 retry 改成 raise，上层没 catch）

### 3. 安全合规 — "绝对不能进主分支的"

- 密钥泄露（硬编码 API key、token、密码）
- 敏感数据暴露（日志打了用户数据、返回值包含不该返回的字段）
- 权限越级（接口没做鉴权就暴露）

### 4. 协作盲区 — "我不知道你也在改这里"

- 并行 PR 冲突（两人改了同一文件的相邻逻辑）
- 重复实现（项目已有类似工具函数，又写了一个）
- 违反团队约定（项目规定用 A 方案，PR 用了 B）
- 历史坑点重犯（同样的改法之前导致过问题）

### 5. 工程健康 — "现在没事，迟早出事"

- 改了代码没改测试
- 新增 TODO 未追踪
- 依赖风险（引入不维护的包、license 不兼容）
- Migration 无 rollback

### 审查原则

**看到证据才报，看不到就不报。** 不猜测，不泛泛地"建议考虑 XXX"。每个发现必须通过工具调用验证。

---

## Agent 循环约束

### 终止条件

Agent 循环在以下任一条件满足时终止：

| 条件 | 处理方式 |
|---|---|
| agent 调用 `finish_review` | 正常结束，输出 review |
| agent 调用 `escalate` | 中断 Flash 循环，升级到 Sonnet |
| 达到最大轮次（15 轮） | 强制终止，基于已收集信息输出 review |
| 累积 input tokens 超过预算（60K） | 强制终止，同上 |
| 连续 3 次调用同一工具且参数相同 | 检测到死循环，强制终止 |

### escalate 交接机制

agent 调用 `escalate(reason)` 后，**框架**（不是 Flash）负责组装交接数据：

1. 从历史 messages 中提取所有工具调用结果
2. 按类别分组压缩为结构化摘要
3. 组装交接 payload 发给 Sonnet

```python
# 伪代码
def handle_escalate(agent_history, pr_context, reason):
    handoff = {
        "pr_info": pr_context.info,
        "diff": pr_context.diff,
        "context_summary": compress_tool_results(agent_history),
        "relevant_code": extract_code_snippets(agent_history),
        "repo_rules": pr_context.rules,
        "risk_reason": reason,
    }
    return call_sonnet(handoff)
```

Flash 只需要说"为什么升级"，交接的脏活框架干。

### 大 Diff 处理

不一次性灌完整 diff。流程：

1. Agent 首先通过 `get_pr_changed_files` 拿到变更文件列表（文件名 + 增删行数）
2. Agent 根据文件列表判断优先级，选择性调用 `get_pr_diff(file_path=...)` 逐文件查看
3. 对于超过 500 行的单文件 diff，`get_pr_diff` 返回时标注 `[truncated]` 并建议 agent 用 `read_file` 读具体区域

这样 agent 自己控制"看什么、看多少"，不会被大 PR 撑爆上下文。

---

## Re-review 流程

### 触发条件

webhook 监听 `pull_request.synchronized` 事件（作者 push 了新 commit）。

### 增量策略

不完全重新 review，而是增量：

1. 取新 push 的 commit 范围（`last_reviewed_sha..HEAD`）的增量 diff
2. 从 PG 加载上次 review 的 comments
3. Agent 带着"上次提了什么问题"+ "新的改动"进入循环
4. 对每个旧 comment 判断：已修复 → 标记 resolved / 未修复 → 重新提醒 / 改错了 → 新评论

### 数据流

```
新 push 触发
    │
    ├→ 查 PG：上次 review 的 comments（WHERE pr_number = ? AND resolved = false）
    ├→ 取增量 diff：base = last_reviewed_sha
    │
    ▼
Agent 循环（额外工具输入：上次未 resolved 的 comments）
    │
    ▼
输出：新 comments + 旧 comments 状态更新（resolved / still open）
```

### resolved 状态同步

`review_comments.resolved` 有两个更新来源：

| 来源 | 触发方式 |
|---|---|
| Re-review | Agent 判断旧 comment 对应的问题已在新 commit 中修复 → `resolved = true` |
| GitHub webhook | 监听 `pull_request_review_comment` 事件，作者在 GitHub 上手动 resolve → 同步到 PG |

两个来源都能标记 resolved，以最新状态为准。

---

## 上下文管理

### 核心原则

代码检索用 grep（精确匹配，实时，零维护），不用 RAG。Agent 从 diff 出发，自己决定搜什么、读什么、追到哪里。

### 工具返回控制

| 工具 | 返回上限 | 超出处理 |
|---|---|---|
| `read_file` | 300 行 | 支持 start_line/end_line，agent 分段读 |
| `search_code` | 前 20 条匹配 | 每条返回匹配行 ± 3 行上下文 |
| `git_log` | 最近 10 条 | 只返回 message + 文件列表 |
| `get_pr_diff` | 按文件查看时不截断；单文件超 500 行时截断并标注 `[truncated]` | agent 可用 `read_file` 读具体区域 |
| `query_review_history` | 前 5 条 | 按相关度排序 |

### 累积上下文压缩

循环第 5 轮时自动触发：将轮次 1-4 的工具结果压缩为结构化摘要，替换原始内容。后续轮次基于摘要 + 新工具结果推理。

### Sonnet 交接

详见"Agent 循环约束 → escalate 交接机制"章节。交接 payload 控制在 15-20K tokens。

---

## 知识系统

### Repo 文件（`.ai-review/`）

```
.ai-review/
├── rules/
│   ├── general.md          # 通用规则
│   └── project-specific.md # 项目特定约定
├── known-issues/
│   └── pitfalls.md         # 历史坑点
└── config.yml              # agent 行为配置
```

`config.yml` 示例：

```yaml
ignore_paths:
  - "*.lock"
  - "generated/**"
  - "docs/**"

tech_stack:
  language: python
  framework: fastapi
  testing: pytest
```

这些文件跟着 repo 走，团队可编辑，CC 读项目文件时也能看到。

### PostgreSQL 数据模型

```sql
-- review 历史记录
reviews (
  id, repo_id, pr_number, author,
  risk_level, summary, model_used,
  reviewed_sha,    -- 本次 review 对应的 commit SHA，re-review 用于取增量 diff
  created_at
)

-- 具体 review 评论
review_comments (
  id, review_id, file_path, line,
  severity, category,
  comment, resolved,
  created_at
)

-- 跨 PR 模式统计（agent 自动维护）
issue_patterns (
  id, repo_id, category, pattern_description,
  occurrence_count, last_seen_at,
  example_prs[]
)

-- agent 推理轨迹（debug 用）
agent_traces (
  id, review_id, round_number,
  tool_called, tool_params, tool_result_summary,
  reasoning, created_at
)
```

### 知识检索策略

| 检索对象 | 方案 | 原因 |
|---|---|---|
| 源代码 | grep（search_code 工具） | 精确匹配，实时，零维护 |
| 历史 review | V1 SQL 结构化查询 | 按 file_path, category, keyword 精确检索 |
| 历史 review（V2） | pgvector 向量检索 | 语义相似性查询（"类似的重构"） |

### 知识流动

```
PR review 完成
    ├→ review + comments → PostgreSQL（积累历史）
    ├→ agent 发现新 pattern（同类问题 ≥ 3 次）
    │       → 自动提 PR 更新 .ai-review/known-issues/pitfalls.md
    └→ agent_traces → PostgreSQL（debug + prompt 优化依据）
```

---

## 多租户设计

| 维度 | 方案 |
|---|---|
| 代码仓库 | GitHub App 安装到 org，自动覆盖所有 repo |
| 知识库 | 每个 repo 自己的 `.ai-review/rules/`，互不干扰 |
| Review 历史 | PG 按 repo_id 隔离 |
| API 额度 | Gateway 按 tenant（每组一个 key）独立限流 |
| 规则定制 | 各组在自己 repo 里写规则，agent 自动识别 |

---

## 部署

### Docker Compose 单机部署

```yaml
services:
  review-agent:
    build: .
    depends_on: [postgres, redis]
    environment:
      - DATABASE_URL=postgresql://...
      - REDIS_URL=redis://redis:6379/0
      - AI_GATEWAY_URL=http://ai-gateway:8080

  celery-worker:
    build: .
    command: celery -A app.core.celery_app worker
    depends_on: [postgres, redis]

  ai-gateway:
    image: ai-api-gateway:latest
    ports: ["8080:8080"]

  postgres:
    image: pgvector/pgvector:pg16
    volumes: [pg_data:/var/lib/postgresql/data]

  redis:
    image: redis:7-alpine
    volumes: [redis_data:/data]

volumes:
  pg_data:
  redis_data:
```

### Gateway 新增 Scenario 配置

```yaml
gateway:
  model-routing:
    code-review-scan:
      primary: gemini-3.0-flash
      fallback: deepseek-v4-flash
      cost-weight: 1
    code-review-reason:
      primary: claude-sonnet-4.6
      fallback: gemini-3.5-flash
      cost-weight: 5
```

---

## 实现优先级

### P0 — 最小可用 Agent

- Agent 循环框架（ReAct loop + tool calling）
- 9 个 P0 工具
- 风险判定 + 双模型路由
- Review 结果发布到 GitHub
- PostgreSQL 基础数据模型（reviews + review_comments）

### P1 — 高价值增强

- 8 个 P1 工具
- agent_traces 记录
- 上下文压缩机制
- `.ai-review/` 规则文件规范定义与文档
- query_review_history 历史检索

### P2 — 协作与智能

- 5 个 P2 工具
- issue_patterns 自动统计
- 高频问题自动沉淀到 repo 文件
- pgvector 语义检索
