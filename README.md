# tg-chinese-search

Telegram 频道中文全文搜索机器人（首阶段仅支持频道，不包含群聊）。

## 功能清单

- 实时接收频道消息：`channel_post` / `edited_channel_post`
- SQLite 存储：原始表 + FTS5 虚拟表
- 中文搜索：`jieba + FTS5 + 2gram`，支持多关键词与频道过滤
- 历史导入：支持 Telegram Desktop `result.json`
- 交互模式：
- 私聊直接输入关键词搜索（支持 `@频道 关键词`）
- Inline 模式（支持 `#频道 关键词`）
- `/search` 指令
- `/start` / `/help` / `/helph`
- 管理控制台（私聊）：
- 白名单 ID + 管理员密码双因子
- 机器人内动态配置（含敏感配置）
- 管理审计日志
- Telegram API 代理支持（仅代理 Bot <-> Telegram）
- Docker 运行支持

## 目录结构

```text
app/
  admin/         # 管理鉴权、动态配置、管理命令
  importer/      # Telegram Desktop JSON 导入
  ingest/        # 频道消息接收与统一入口
  interaction/   # 私聊、inline、命令交互
  network/       # 代理注入
  normalize/     # 消息规范化
  search/        # 分词与查询
  storage/       # SQLite schema 与 repository
  utils/         # 链接工具等
docs/
  webhook-self-signed.md
tests/
scripts/
  generate_secrets.py
```

## 环境要求

- Python 3.11+
- SQLite（Python 内置）

## 快速开始（venv）

1. 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

2. 生成管理员密码 hash 和加密密钥

```powershell
python scripts/generate_secrets.py "你的管理员密码"
```

3. 配置环境变量

```powershell
Copy-Item .env.example .env
# 编辑 .env，至少填 BOT_TOKEN / ADMIN_IDS / ADMIN_PASSWORD_HASH / CONFIG_ENCRYPTION_KEY
```

4. 运行机器人（polling）

```powershell
python -m app.main run
```

## 历史消息导入

```powershell
python -m app.main import --json "C:\path\to\result.json"
```

仅预览不写库：

```powershell
python -m app.main import --json "C:\path\to\result.json" --dry-run
```

## 搜索语法

- 私聊：
- `你好世界`
- `@mychannel 你好`
- inline：
- `@botname 关键词`
- `@botname #mychannel 关键词`
- 命令：
- `/search 关键词`
- `/search @mychannel 关键词`
- `/start`
- `/help`（`/helph` 也可）

## 管理命令（仅私聊）

- `/admin_login <password>`
- `/admin_set <key> <value>`
- `/admin_get <key>`
- `/admin_list`
- `/admin_logout`
- `/admin_apply`

说明：

- 只有 `ADMIN_IDS` 白名单用户可登录。
- 必须密码正确才会建立会话。
- 登录失败有锁定机制（默认 5 次失败锁 10 分钟）。
- 配置变更会写入 `admin_audit_log`。

## 动态配置键（app_config）

- `telegram_proxy_enabled` (`true`/`false`)
- `telegram_proxy_url`
- `bot_token`
- `sqlite_path`
- `webhook_url`
- `webhook_listen_host`
- `webhook_listen_port`
- `default_search_limit`
- `private_page_size`
- `private_separator`

敏感项会加密存储，展示时脱敏。

分割线示例（运行中可改）：

```text
/admin_set private_separator ----
```

分页会显示 `当前页/总页数`。

私聊内容摘要规则：

- 以第一个关键词为中心，截取前 12 字 + 后 25 字（约 50 字）
- 若未命中关键词位置则回退到普通 50 字截断

链接生成策略：

- 优先使用导入消息自带 `link`
- 其次使用 `@channel_username/message_id`
- 最后尝试 `t.me/c/...` 形式链接

内联结果发送后会带“查看原文”按钮（而不是正文内链接）。

## 代理配置（Telegram API Only）

`.env` 示例：

```env
TELEGRAM_PROXY_ENABLED=true
TELEGRAM_PROXY_URL=socks5://user:pass@127.0.0.1:1080
PROXY_FAIL_OPEN=true
```

也可运行中设置：

```text
/admin_set telegram_proxy_enabled true
/admin_set telegram_proxy_url socks5://user:pass@127.0.0.1:1080
/admin_apply
```

## Webhook（自签证书）

请看：`docs/webhook-self-signed.md`

文档包含：

- 云服务器直连 HTTPS（自签）
- Nginx 反代 HTTPS（自签）
- 代理与 webhook 共存说明
- 故障排查矩阵

## Docker 运行

1. 编辑 `.env`
2. 构建并启动

```powershell
docker compose up -d --build
```

3. 查看日志

```powershell
docker compose logs -f
```

## 测试

```powershell
pytest -q
```

## 安全建议

- 管理员密码使用强密码，且定期更换。
- `CONFIG_ENCRYPTION_KEY` 不要提交到仓库。
- `ADMIN_IDS` 只保留必要账号。
- 定期审计 `admin_audit_log`。
