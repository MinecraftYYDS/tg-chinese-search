# 对外搜索 API 文档

该 API 用于给其他程序提供只读搜索能力，与 Telegram Bot 同进程运行。

## 基本信息

- Base URL：`http://<EXTERNAL_API_HOST>:<EXTERNAL_API_PORT>`
- 搜索接口：`GET /api/search`
- 随机接口：`GET /api/random`
- 健康检查：`GET /healthz`
- 编码：UTF-8
- 响应格式：JSON

## 开关与鉴权

运行时配置键（`app_config`）：

- `external_api_enabled`：是否启用对外 API（`true/false`）
- `external_api_token`：访问 token（可空）

鉴权规则：

1. `external_api_enabled=false` 时，`/api/search` 与 `/api/random` 返回 `503`。
2. `external_api_enabled=true` 且 `external_api_token` 为空时，允许匿名访问。
3. `external_api_enabled=true` 且 `external_api_token` 非空时，必须传：

```http
Authorization: Bearer <token>
```

## 请求参数

`GET /api/search`

| 参数 | 必填 | 类型 | 默认值 | 说明 |
|---|---|---|---|---|
| `q` | 是 | string | - | 搜索关键词，不能为空 |
| `channel` | 否 | string | `null` | 频道过滤，支持 `@name` / `#name` / chat_id |
| `limit` | 否 | int | `default_search_limit` | 返回条数，上限 200 |
| `offset` | 否 | int | `0` | 分页偏移，需 >= 0 |

`GET /api/random`

| 参数 | 必填 | 类型 | 默认值 | 说明 |
|---|---|---|---|---|
| `channel` | 否 | string | `null` | 频道过滤，支持 `@name` / `#name` / chat_id |
| `limit` | 否 | int | `default_random_limit` | 返回条数，上限 `max_random_limit`（默认 10） |

说明：`/api/random` 不支持关键词参数 `q`，仅支持全局随机或按频道随机。

## 响应格式

统一结构：

```json
{
  "code": "ok",
  "message": "ok",
  "data": {}
}
```

### 成功示例

```json
{
  "code": "ok",
  "message": "ok",
  "data": {
    "q": "你好",
    "channel": "@mychannel",
    "limit": 10,
    "offset": 0,
    "total": 123,
    "items": [
      {
        "id": 1,
        "chat_id": -100123,
        "message_id": 456,
        "channel_username": "mychannel",
        "source_link": "https://t.me/mychannel/456",
        "text": "你好，世界",
        "timestamp": 1730000000
      }
    ]
  }
}
```

`GET /api/random` 成功示例：

```json
{
  "code": "ok",
  "message": "ok",
  "data": {
    "channel": "@mychannel",
    "limit": 3,
    "total": 123,
    "items": [
      {
        "id": 1,
        "chat_id": -100123,
        "message_id": 456,
        "channel_username": "mychannel",
        "source_link": "https://t.me/mychannel/456",
        "text": "你好，世界",
        "timestamp": 1730000000
      }
    ]
  }
}
```

## 错误码

| HTTP Status | code | 说明 |
|---|---|---|
| `400` | `invalid_query` | 缺少或空 `q` |
| `400` | `invalid_params` | 参数格式错误（如 `limit<=0`、`offset<0`） |
| `401` | `unauthorized` | token 缺失或错误 |
| `404` | `not_found` | 路径不存在 |
| `503` | `api_disabled` | API 已关闭 |
| `500` | `internal_error` | 服务内部异常 |

## 调用示例

匿名访问：

```bash
curl "http://127.0.0.1:8787/api/search?q=你好&limit=10&offset=0"

curl "http://127.0.0.1:8787/api/random?limit=3&channel=@mychannel"
```

Bearer Token：

```bash
curl -H "Authorization: Bearer your-token" "http://127.0.0.1:8787/api/search?q=你好"

curl -H "Authorization: Bearer your-token" "http://127.0.0.1:8787/api/random?limit=2"
```

## 运行时配置示例

通过 bot 管理命令立即生效（无需重启）：

```text
/admin_set external_api_enabled true
/admin_set external_api_token your-token
```

禁用 API：

```text
/admin_set external_api_enabled false
```

## 运维注意事项

- 建议将 `EXTERNAL_API_HOST` 绑定为 `127.0.0.1`，并通过反向代理对外暴露。
- 若直接公网暴露，强烈建议配置 `external_api_token`。
- 日志不会打印 token，但请避免在代理层记录完整授权头。
