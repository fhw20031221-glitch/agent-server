# Ringo Agent Server

Ringo Agent Server 是 Ringo 全栈 Agent 项目的远程服务端。它基于 FastAPI 构建，负责用户认证、月度 Token 配额、聊天会话、模型配置和 OpenAI-compatible SSE Agent Turn。

关联仓库：

| 仓库 | 职责 |
|---|---|
| `agent-desktop` | Electron 桌面客户端、本地工作区索引、MCP 工具、编辑预览与应用 |
| `agent-server` | FastAPI 服务端、认证、配额、会话、模型管理、OpenAI-compatible SSE Agent Turn |

## 全栈协作方式

1. 用户在桌面端登录并打开本地项目目录。
2. 桌面端生成 Repo Map，并注册本地工作区 MCP 工具。
3. 桌面端向服务端 `/agent/turn/stream` 发起 SSE 请求，携带会话、模型、消息与工具定义。
4. 服务端负责鉴权、模型选择、额度检查、上游模型流式调用和用量入库。
5. 模型需要代码上下文时，服务端通过 SSE 下发 tool call；桌面端在本地执行工具后继续下一轮 Agent Turn。
6. Code 模式下，模型只能生成结构化编辑预览；最终文件写入由桌面端校验哈希后执行。

服务端不会直接读取用户本地工作区，所有本地文件搜索、读取和编辑预览都在 `agent-desktop` 内执行。

## 功能

- 用户注册、登录、刷新 token、退出登录
- Refresh token 哈希存储，access token 短期有效
- 月度 Token 配额、用量统计和管理员额度调整
- 聊天会话与消息持久化
- `/agent/turn/stream` SSE Agent Turn
- 支持 `partial`、`reasoning`、`tool_call`、`working`、`result` 事件
- OpenAI-compatible Chat Completions 上游调用
- 用户可选模型列表，管理员可维护模型配置
- 支持从百炼 OpenAI-compatible `/models` 接口同步模型
- MySQL 8 数据持久化，Alembic 数据库迁移

## 技术栈

- FastAPI：HTTP API 与 SSE
- SQLAlchemy 2 + PyMySQL：数据访问
- Alembic：数据库迁移
- Pydantic Settings：环境变量配置
- PyJWT + Argon2：JWT 与密码哈希
- httpx：OpenAI-compatible 上游请求
- MySQL 8：默认数据库
- Docker Compose + Caddy：容器化与反向代理

## 项目结构

```text
├── app/
│   ├── api/routes/           # auth、quota、chat、agent、models、admin 路由
│   ├── core/                 # 配置与安全工具
│   ├── db/                   # SQLAlchemy model 与 session
│   ├── schemas/              # Pydantic 请求/响应模型
│   ├── services/             # 认证、配额、会话、模型、LLM 服务
│   ├── scripts/              # 管理脚本
│   └── utils/sse.py          # SSE 事件格式化
├── alembic/                  # 数据库迁移
├── deploy/                   # Caddy / Nginx 部署配置
├── tests/                    # pytest 测试
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## 运行

### 方式一：Docker Compose

```powershell
cd E:\codes\agent-server
Copy-Item .env.example .env
docker compose up --build
```

默认编排：

| 服务 | 说明 |
|---|---|
| `db` | MySQL 8 |
| `agent-server` | FastAPI 应用，容器内监听 8000 |
| `caddy` | 反向代理，对外暴露 8080 / 8443 |

### 方式二：本地 Python + Compose MySQL

```powershell
cd E:\codes\agent-server
Copy-Item .env.example .env
docker compose up -d db
```

本地进程访问 Compose MySQL 时，把 `.env` 中的数据库地址改为宿主机端口：

```env
AGENT_SERVER_DATABASE_URL=mysql+pymysql://agent:agent@127.0.0.1:3307/agent?charset=utf8mb4
```

然后启动服务：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 测试

```powershell
pytest
```

## 环境变量

| 变量 | 说明 |
|---|---|
| `AGENT_SERVER_ENV` | 运行环境 |
| `AGENT_SERVER_HOST` | 服务监听地址 |
| `AGENT_SERVER_PORT` | 服务监听端口 |
| `AGENT_SERVER_DATABASE_URL` | MySQL 连接串 |
| `AGENT_SERVER_JWT_SECRET` | JWT 签名密钥，生产环境必须修改 |
| `AGENT_SERVER_ACCESS_TOKEN_EXPIRE_MINUTES` | Access token 有效期 |
| `AGENT_SERVER_REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token 有效期 |
| `AGENT_SERVER_DEFAULT_MONTHLY_TOKEN_LIMIT` | 新用户默认月度 Token 额度 |
| `AGENT_SERVER_OPENAI_BASE_URL` | 默认 OpenAI-compatible API Base URL |
| `AGENT_SERVER_OPENAI_API_KEY` | 默认上游 API Key |
| `AGENT_SERVER_OPENAI_MODEL` | 默认模型名 |
| `AGENT_SERVER_OPENAI_TIMEOUT_SECONDS` | 上游请求超时时间 |
| `AGENT_SERVER_ADMIN_USERNAME` | 首次启动时创建初始管理员 |
| `AGENT_SERVER_ADMIN_PASSWORD` | 初始管理员密码 |
| `AGENT_SERVER_ADMIN_EMAIL` | 初始管理员邮箱 |
| `AGENT_SERVER_CORS_ORIGINS` | 允许的跨域来源，逗号分隔 |
| `DASHSCOPE_API_KEY` | 百炼模型同步使用的 API Key |

## 主要接口

| 接口 | 用途 |
|---|---|
| `GET /healthz` | 健康检查 |
| `POST /auth/register` | 注册 |
| `POST /auth/login` | 登录并获取 access token / refresh token |
| `POST /auth/refresh` | 轮换 refresh token 并刷新 access token |
| `POST /auth/logout` | 注销 refresh token |
| `GET /auth/me` | 当前用户信息 |
| `GET /quota/me` | 当前用户额度 |
| `POST /chat/sessions` | 创建聊天会话 |
| `GET /chat/sessions` | 查询会话列表 |
| `GET /chat/sessions/{session_id}/messages` | 查询会话消息 |
| `POST /agent/turn/stream` | Agent Turn SSE，桌面端主调用链路 |
| `GET /models` | 用户可见模型列表 |
| `GET /admin/users` | 管理员查询用户 |
| `GET /admin/users/{user_id}` | 管理员查询单个用户 |
| `PATCH /admin/users/{user_id}/quota-limit` | 管理员设置月度额度 |
| `PATCH /admin/users/{user_id}/remaining-quota` | 管理员校准剩余额度 |
| `POST /admin/users/{user_id}/quota-adjustments` | 管理员追加额度调整记录 |
| `PATCH /admin/users/{user_id}/status` | 管理员启用或停用用户 |
| `GET /admin/models` | 管理员查询模型配置 |
| `POST /admin/models` | 管理员新增模型配置 |
| `POST /admin/models/sync` | 管理员从远程 `/models` 同步模型 |
| `PATCH /admin/models/{model_id}` | 管理员更新模型配置 |
| `DELETE /admin/models/{model_id}` | 管理员删除模型配置 |

## Agent Turn SSE

`POST /agent/turn/stream` 接收桌面端传入的消息与工具定义，服务端流式返回：

| 事件类型 | 说明 |
|---|---|
| `working` | Agent 进度步骤 |
| `partial` | 当前累计回答文本 |
| `reasoning` | 上游模型返回的思考过程，默认折叠 |
| `tool_call` | 模型请求桌面端执行本地 MCP 工具 |
| `result` | 本轮结果、工具调用列表、用量统计、剩余额度 |
| `error` | 错误信息 |

当上游模型返回 `reasoning_content` 时，服务端会额外发送：

```json
{"type":"reasoning","text":"累计思考过程","collapsed":true}
```

未返回思考过程的模型不会发送该事件，保持普通 `partial/result` 行为。

## 百炼模型同步

在 `.env` 中配置百炼 API Key：

```env
DASHSCOPE_API_KEY=你的百炼APIKey
```

管理员调用：

```bash
curl -X POST http://127.0.0.1:8000/admin/models/sync \
  -H "Authorization: Bearer <admin_access_token>" \
  -H "Content-Type: application/json" \
  -d '{"provider":"bailian","api_key_env":"DASHSCOPE_API_KEY"}'
```

服务端会从 `https://dashscope.aliyuncs.com/compatible-mode/v1/models` 拉取模型列表，并写入 `llm_models`。用户聊天时传 `model_key` 即可切换模型；不传则使用默认模型。

## 安全设计

- 服务端不读取用户本地工作区
- 用户本地工具调用只在桌面端执行
- Refresh token 只存哈希，不存明文
- Access token 短期有效，refresh token 轮换
- 密码使用 Argon2 哈希
- 用量入库后扣减月度 Token 额度
- 管理员接口必须使用管理员身份
- 生产环境必须修改 `AGENT_SERVER_JWT_SECRET` 并通过 HTTPS 暴露
