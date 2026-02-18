# 自签证书 Webhook 使用指南（云服务器 + Nginx）

本文档只讨论 Telegram Bot Webhook 的 HTTPS 接入，自签证书可用。

## 前置说明

- Telegram webhook 入站要求 HTTPS。
- 本项目中的代理仅影响 Bot 到 Telegram 的出站 API 请求，不影响 webhook 的入站 TLS。
- 推荐开放端口：`443`（最通用）。

---

## 方案 A：云服务器直连 HTTPS（Bot 自己监听 TLS）

### 1. 生成自签证书

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout certs/key.pem \
  -out certs/cert.pem \
  -days 365 \
  -subj "/CN=your.domain.com"
```

说明：

- `CN` 建议使用域名（或公网 IP）。
- 证书主题需与 webhook URL 主机名一致。

### 2. 配置 `.env`

```env
APP_MODE=webhook
WEBHOOK_URL=https://your.domain.com:8443
WEBHOOK_LISTEN_HOST=0.0.0.0
WEBHOOK_LISTEN_PORT=8443
WEBHOOK_CERT_PATH=certs/cert.pem
WEBHOOK_KEY_PATH=certs/key.pem
```

### 3. 启动服务

```bash
python -m app.main run
```

### 4. 注册 webhook（上传自签公钥证书）

```bash
curl -F "url=https://your.domain.com:8443" \
     -F "certificate=@certs/cert.pem" \
     "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook"
```

### 5. 验证

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```

检查：

- `url` 是否正确
- `last_error_message` 是否为空

---

## 方案 B：Nginx 反代 HTTPS（Nginx 终止 TLS，自签）

### 1. 生成证书

同方案 A，得到 `cert.pem` / `key.pem`。

### 2. Bot 使用 HTTP 本地监听

`.env` 示例：

```env
APP_MODE=webhook
WEBHOOK_URL=https://your.domain.com
WEBHOOK_LISTEN_HOST=127.0.0.1
WEBHOOK_LISTEN_PORT=8080
WEBHOOK_CERT_PATH=
WEBHOOK_KEY_PATH=
```

### 3. Nginx 配置

`/etc/nginx/conf.d/tg-bot.conf`：

```nginx
server {
    listen 443 ssl;
    server_name your.domain.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 90s;
    }
}
```

应用配置：

```bash
nginx -t && systemctl reload nginx
```

### 4. 注册 webhook（仍需上传证书）

```bash
curl -F "url=https://your.domain.com" \
     -F "certificate=@/path/to/cert.pem" \
     "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook"
```

---

## 代理与 Webhook 共存

- `TELEGRAM_PROXY_ENABLED=true` 仅影响 Bot 出站请求。
- Webhook 入站链路是 Telegram -> 你的 HTTPS 端点，不经过该代理。

---

## 故障排查矩阵

### 1. 证书问题

- 症状：`getWebhookInfo` 出现 TLS 相关错误
- 检查：
- CN/SAN 与 URL 主机名一致
- 上传的是正确公钥证书（`cert.pem`）
- 证书未过期

### 2. Nginx 反代问题

- 症状：Webhook 超时、502、更新收不到
- 检查：
- `proxy_pass` 地址和 Bot 监听端口一致
- 防火墙已放行 443
- `nginx -t` 通过，日志无报错

### 3. 代理问题（仅 Telegram API）

- 症状：Bot 无法调用 Telegram API（发送消息失败）
- 检查：
- `telegram_proxy_url` 格式正确（socks5/http）
- 代理可达
- 临时关闭代理验证是否恢复

---

## 常用命令

删除 webhook 回到 polling：

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/deleteWebhook?drop_pending_updates=true"
```

查看 webhook 状态：

```bash
curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getWebhookInfo"
```
