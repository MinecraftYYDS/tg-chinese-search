# V2：WebUI、指定频道采集与部署

## 启动

Python 3.11+、Node 22.12+（仅构建前端时需要）：

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate；Linux: source .venv/bin/activate
pip install -r requirements.txt
cd web
npm ci
npm run build
cd ..
python -m app.main serve --host 127.0.0.1 --port 8787
```

打开 WebUI，用 `data/bootstrap-token.txt` 中的一次性凭据创建管理员。之后在「服务设置」配置 API ID / Hash，在「Telegram 账号」扫码或手机号登录，再添加指定频道。API ID / Hash 在 https://my.telegram.org/apps 申请。

所有新频道默认仅管理员可见。需要给 TG 搜索机器人、QQ bot 查询时，明确开启该频道「对外查询」。没有配置的频道不会被采集，也不会经外部接口返回。

私有频道必须先用采集账号加入，再通过 WebUI 的「选择已加入的频道」选择。不会自动接受邀请或加入频道。仅支持广播频道，不支持群聊、私聊，不下载媒体。

## Docker + Nginx

```sh
install -d -m 700 -o 10001 -g 10001 data
docker compose up -d --build
```

容器以 UID 10001 运行，宿主机只绑定 `127.0.0.1:8787`。**不要将 8787 对公网开放**。部署前端和 API 共用域名，参考 `deploy/nginx.conf`，对外使用 HTTPS 443；80 仅用于证书校验和跳转。

`TG_PUBLIC_ORIGIN` 必须是最终 HTTPS 源地址，例如 `https://keaixm.awacat.cc`；本机开发可设为 `http://127.0.0.1:8787`。跨站写请求被拒绝。代理必须覆盖 `Host`、`X-Forwarded-Proto`，不能透传客户端伪造的头。

`TG_FORWARDED_ALLOW_IPS` 默认仅信任本机。Compose 设置为 `*` 是因为后端仅通过宿主机 loopback 发布，并位于 Docker 隔离网络；**不要将此设置用于直接公网暴露的后端**。

Cloudflare 必须使用 **Full (strict) / 完全（严格）** 回源，不能使用 Flexible / 灵活，否则 HTTP→HTTPS 跳转循环，且回源链路不安全。可对该主机名单独设置规则，不必改变其他站点。

### 可选部署参数

| 参数 | 默认 | 作用 |
|---|---|---|
| `TG_DATA_DIR` | `data` | 运行数据、自动生成主密钥、初始化凭据目录 |
| `SQLITE_PATH` | `<TG_DATA_DIR>/tg_search.db` | 兼容旧库路径 |
| `CONFIG_ENCRYPTION_KEY` | 自动生成 `master.key` | 旧版密钥迁移，不能遗失 |
| `TG_PUBLIC_ORIGIN` | 请求自身源 | 正式部署应显式指定 HTTPS 源 |
| `TG_FORWARDED_ALLOW_IPS` | `127.0.0.1` | 信任的反向代理来源 |

**一个数据目录只能运行一个服务实例、一个 ASGI worker。** Telegram session 加密保存在数据库，不需要额外 `.session` 文件。账号授权失效时会暂停采集并提示重新登录；已有数据查询不受影响。

## API 与 bot 联动

在 WebUI 生成 API Token（仅显示一次），开启外部 API，然后开启需要提供内容的频道「对外查询」。

```sh
curl -H 'Authorization: Bearer YOUR_TOKEN' 'https://keaixm.awacat.cc/api/random?limit=1'
curl -H 'Authorization: Bearer YOUR_TOKEN' 'https://keaixm.awacat.cc/api/search?q=你好&limit=10&offset=0'
```

`GET /healthz` 不要求 Token，仅表示 Web 服务存活；不代表 Telegram 账号已连接。

本次为 `bot` 增加向后兼容配置：

```json
{
  "EXTERNAL_API_BASE_URL": "https://keaixm.awacat.cc",
  "EXTERNAL_API_TOKEN": "在 WebUI 生成的 Token"
}
```

也可在 bot 的 WebUI 全局设置填写完整地址。新地址优先；留空仍使用原来的 `EXTERNAL_API_HOST` / `EXTERNAL_API_PORT` HTTP 配置。同机部署可继续 `127.0.0.1:8787`。更改 bot 代码后需要按其原有方式更新/重启，本项目不会自动重启未知位置的 bot。

兼容原接口外层 `code/message/data`、`items/total`、消息字段、错误码和条数限制。新 Web 服务出于安全考虑始终要求 Bearer Token，不再提供匿名外部查询。

## 同步语义

- 历史回填和增量补漏使用独立持久化游标，实时事件不会跳过中间消息。
- 每批数据与历史/增量游标同一事务提交；崩溃后可续传。
- 无文本消息也推进游标。编辑时间防止旧历史覆盖新编辑。
- 账号只请求配置且启用采集的频道消息；查看「已加入频道」仅列举元数据供手动选择。
- 新增、编辑实时处理；启动/重连主动补漏，完成回填后约每 30 秒检查新消息。
- 接收删除事件，每半小时复核最近最多 100 条已存储消息。不能保证发现所有过去错过的删除、编辑；消息 ID 缺口不视为删除。
- 屏蔽记录独立存储，重抓、重新编辑不取消屏蔽。
- Telegram 限流遵守等待时间，频道无权限时暂停该频道。
- 暂停采集不等于关闭对外查询；两个开关独立。

## 旧数据迁移

先停旧服务、备份旧库和原 `CONFIG_ENCRYPTION_KEY`，在副本验证后迁移。将旧密钥设置为环境变量（Compose 需显式传入），或写入受保护的 `data/master.key`。不能为旧加密配置随意生成新密钥。

V2 首次启动保留消息并建立迁移版本；旧频道生成「待验证、暂停采集、不对外查询」配置。管理员在登录采集账号后重新添加/验证这些频道，再明确启用对外查询。旧库里最大的消息 ID 不视为完整同步游标，仍会补齐历史并去重。

旧 JSON 导入命令仍保留，但 V2 运行中不应另起导入进程写同一数据库。应先停止服务，完成导入后再启动并在 WebUI 验证频道。

## 备份恢复

```sh
python scripts/backup.py --data data --output /secure/backups/tg-search-2026-10-15.tar.gz
```

脚本使用 SQLite backup API 创建一致性快照，并打包 `master.key`。加密 session 已包含在数据库；备份同时含解密密钥，必须像密码一样保护，不应放在 Web 根目录或公开下载目录。不支持自定义数据库路径的自动推断，使用自定义路径时需对应备份。

恢复时停止服务，将归档中的 `data/tg_search.db`、`data/master.key` 恢复到**新的空数据目录**，设置正确权限再启动。不要只替换运行中的主数据库而保留旧 WAL/SHM。外部环境变量主密钥需要单独备份，脚本在没有 `master.key` 时拒绝继续。

回滚使用切换前的旧服务备份，不应让旧程序直接写已升级的数据目录。不要让新旧实例同时使用同一 Telegram session。

## 测试

```sh
python -m pytest -q
cd web
npm run build
npx playwright install chromium
npx playwright test
```

真实 Telegram 扫码、验证码、两步验证和频道抓取，需要管理员自己的应用凭据及授权账号验收；自动化测试不使用真实账号或发送验证码。
