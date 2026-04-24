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
- `GET /models`
- `POST /admin/models/sync`
- `GET /healthz`

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

## 思考过程 SSE

当上游模型返回 `reasoning_content` 时，服务端会额外发送：

```json
{"type":"reasoning","text":"累计思考过程","collapsed":true}
```

最终 `result` 也会包含 `reasoning.content` 与 `reasoning.collapsed`。未返回思考过程的模型不会发送该事件，保持原有 `partial/result` 行为。

## 说明

- 不读取用户本地工作区
- 不支持本地模型
- refresh token 只存哈希，不存明文
- 生产环境必须通过 HTTPS 暴露
