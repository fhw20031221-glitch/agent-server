# agent-server

独立的远程 Agent 服务端，负责用户认证、月度 Token 配额、聊天会话与 OpenAI-compatible SSE 问答接口。

当前默认数据库为 `MySQL 8`。

## 运行

```powershell
Copy-Item .env.example .env
docker compose up -d db
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## Docker Compose

```powershell
docker compose up --build
```

默认编排：

- `db`：MySQL 8
- `agent-server`：FastAPI
- `caddy`：HTTPS 反向代理

## 主要接口

- `POST /auth/register`
- `POST /auth/login`
- `POST /auth/refresh`
- `POST /auth/logout`
- `GET /auth/me`
- `GET /quota/me`
- `POST /chat/sessions`
- `GET /chat/sessions`
- `GET /chat/sessions/{session_id}/messages`
- `POST /chat/sessions/{session_id}/ask/stream`
- `GET /healthz`

## 说明

- 第一阶段仅支持 ask-only
- 不读取用户本地工作区
- 不支持本地模型
- refresh token 只存哈希，不存明文
- 生产环境必须通过 HTTPS 暴露
