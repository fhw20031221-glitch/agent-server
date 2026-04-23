# 私有证书 IP 部署

适用场景：

- 没有域名
- 宝塔已有站点占用了 `80/443/8080`
- 只想让 `agent-server` 通过私有证书加密对外提供服务

当前约定：

- 对外 HTTPS 端口：`18443`
- 桌面端固定连接：`https://124.221.83.225:18443`
- 桌面端始终允许私有证书 TLS，不再支持自定义服务端地址

## 1. 准备 `.env`

先复制：

```bash
cp .env.example .env
```

然后至少修改：

```env
AGENT_SERVER_ENV=production
AGENT_SERVER_DATABASE_URL=mysql+pymysql://agent:你的数据库密码@db:3306/agent?charset=utf8mb4
AGENT_SERVER_JWT_SECRET=换成至少32字节以上随机长串
AGENT_SERVER_OPENAI_BASE_URL=https://api.deepseek.com/v1
AGENT_SERVER_OPENAI_API_KEY=你的DeepSeekKey
AGENT_SERVER_OPENAI_MODEL=deepseek-chat
```

## 2. 生成私有证书

在项目根目录执行：

```bash
chmod +x deploy/nginx/generate-ip-cert.sh
./deploy/nginx/generate-ip-cert.sh 124.221.83.225
```

生成结果会放到：

- `deploy/nginx/certs/fullchain.pem`
- `deploy/nginx/certs/privkey.pem`

## 3. 启动服务

```bash
docker compose -f docker-compose.private-ssl.yml up -d --build
```

## 4. 验证

服务器本机：

```bash
curl -k https://127.0.0.1:18443/healthz
```

外部机器：

```bash
curl -k https://124.221.83.225:18443/healthz
```

正常应返回：

```json
{"status":"ok"}
```

## 5. 防火墙 / 安全组

需要放行：

- `18443/tcp`

不需要对公网放行：

- `3306`
- `3307`
- `8000`

## 6. 日志

查看服务端日志：

```bash
docker compose -f docker-compose.private-ssl.yml logs -f agent-server
```

查看 Nginx 日志：

```bash
docker compose -f docker-compose.private-ssl.yml logs -f nginx
```
