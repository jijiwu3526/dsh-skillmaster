# dsh-local-bridge

让本机 CLI **自行取得** DSH Web 的带 token URL，不再需要人工从终端复制。

## 为什么需要

DSH 启动时打印一个带一次性 token 的 URL，之后所有 `/api` 请求靠它换发的
HttpOnly Cookie 鉴权。进程外的命令行工具因此需要那个 URL——但目前唯一的获取
方式是人从终端抄下来，于是 token 会进入 shell 历史、CI 日志和聊天记录。

这个插件跑在 DSH **进程内部**，那里 `ctx.connection.authenticatedUrl()` 可以
随时铸造一个新鲜有效的 URL。

## 用法

```bash
python3 dsh_bridge.py            # 显示获取方式（URL 脱敏）
python3 dsh_bridge.py --quiet    # 直接输出 URL
python3 dsh_session.py presets   # 省略 --url 时自动走桥接
```

## 安全模型

路由会交出凭据，因此有三层防护：

1. **仅 loopback**：`Host` 头必须是 loopback 字面量。经隧道或 DNS rebinding
   进来的请求在读取 token 之前就被拒绝。
2. **每次启动的共享密钥**：插件启动时随机生成一个 32 字节密钥，写入
   `$DSH_HOME/local-bridge.secret`（`0600`）。调用方必须回传它，
   这挡住了任何与 DSH 同时启动的本地进程。
3. **不落盘、不记录**：带 token 的 URL 只在响应里返回一次，不写任何文件。

**不防御什么**：以同一用户身份运行、且能读取密钥文件的进程。
但这样的进程本来就能附着到 DSH 进程上。威胁模型是「走失的本机工具与意外泄露」，
不是「已在我账号下运行的恶意软件」。

## 降级

插件缺失或 DSH 未运行时，按顺序回落：

1. `DSH_WEB_URL` 环境变量
2. 无 token 的本机地址（需要已缓存的 Cookie 有效）

## 卸载

```bash
dsh plugin --profile web remove dsh-local-bridge
```

然后完整重启 DSH。密钥文件在进程退出时自动删除。

⚠️ 不要直接 `rm` 掉 `node_modules` 里的目录再手改 `cordis.patch.yml`——
那是本项目早期踩过的坑：DSH 完全看不到手工装的东西，`dsh plugin list` 认不出它，
后续任何 pnpm 操作都可能把它清掉。用 `dsh plugin add` / `remove`。
