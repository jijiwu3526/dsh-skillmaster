# 安装 dsh-local-bridge

让本机 CLI 自行取得 DSH Web 的带 token URL —— 不再需要人工从终端复制。

## 一句话装法

把这个链接丢给你的 DSH 会话：

```
https://github.com/jijiwu3526/dsh-skillmaster
安装其中的 dsh-local-bridge 插件，并告诉我它是否生效。
```

会话会执行下面这一步（**唯一的一步**）：

```bash
dsh plugin --profile web add github:jijiwu3526/dsh-skillmaster#subdir=dsh_bridge/plugin
```

然后**重启 DSH**。

## 为什么只需要这一步

`dsh plugin add` 会自动完成三件事：

1. 装包到 `~/.dsh/profiles/web/node_modules/`
2. 把包登记进 `package.json` 的 `dependencies`
3. 因为该包声明了 `dsh.bundle.patch`，**自动挂进 `dsh.profile.bundles` 层栈**

第 3 点是关键。DSH 的文档原话：

> a dependency resolving to a package that declares `dsh.bundle` joins the layer
> stack (appended in dependency order)

所以**你不需要手改任何配置文件**。

⚠️ 反过来说：如果你只是把插件目录复制到 `node_modules/`，DSH **完全看不到它**——
实测确认配置合成结果里插件条目为 0。同理，手工往 `cordis.patch.yml` 里插条目
虽然能生效，但那是脆弱的：`dsh plugin list` 认不出它，后续任何 pnpm 操作都可能
把它清掉。**请务必用 `dsh plugin add`。**

## 验证装好了

```bash
# 1. DSH 能看到它
dsh plugin --profile web list | grep dsh-local-bridge

# 2. 配置能合成（应输出非 0）
dsh --profile web --dump-config | grep -c dsh-local-bridge

# 3. 桥接真的能拿到带 token 的 URL
python3 dsh_bridge.py
#   期望： "source": "bridge-plugin"
```

## 装完之后

`dsh_session.py` 和 `dshstudio` 会**自动**走桥接，不再需要传 `--url`
或设 `DSH_WEB_URL`：

```bash
python3 dsh_session.py presets                 # 直接能跑
python3 -m dshstudio.cli create --preset <id> --text "开工" --workspace-id <id>
```

## 安全模型

路由会交出凭据，所以有三层防护：

| 防护 | 作用 |
| --- | --- |
| **仅 loopback** | `Host` 头必须是 loopback 字面量。隧道 / DNS rebinding 的请求在铸造 token 前就被拒 |
| **每次启动的共享密钥** | 启动时生成 32 字节随机密钥，写入 `~/.dsh/local-bridge.secret`（`0600`），调用方须回传 |
| **URL 不落盘** | 带 token 的 URL 只在响应体里出现一次，不写文件、不进日志 |

**不防御**：以同一用户身份运行、且能读密钥文件的进程。但那种进程本来就能
附着到 DSH 进程上。威胁模型是「走失的本机工具与意外泄露」，不是「已在你账号
下运行的恶意软件」。

## 卸载

```bash
dsh plugin --profile web remove dsh-local-bridge
```

然后重启 DSH。密钥文件在进程退出时自动删除。

## 前提

- DSH **≥ 0.1.5-rc.1**（插件声明的 `engines`）
- 装在 **web profile** —— 它依赖 `webserver` 与 `connection` 两个宿主服务

如果你用的是别的 profile（例如 `dsh --profile headless`），把命令里的
`web` 换成对应名字即可，但该 profile 需提供这两个服务。

## 故障排查

| 现象 | 原因 |
| --- | --- |
| `dsh plugin list` 里没有它 | 没走 `dsh plugin add`，只是复制了目录 |
| `--dump-config` 里找不到 | 同上，或装完没重启 |
| 桥接返回 403 `bad-secret` | `~/.dsh/local-bridge.secret` 里的密钥已过期（DSH 重启过），重新读一次即可；程序会自动重读 |
| 桥接返回 404 | 插件没加载。检查 `dsh --profile web --dump-config` |
| 桥接返回 401 `unauthorized` | 路由被误挂在 `/api` 下。必须是 `/local-bridge/auth` |

## 零依赖

插件只用 Node 内置模块（`node:fs`、`node:path`、`node:os`），
`dependencies` / `peerDependencies` 全空，也不 import 任何 `@deepseek-ai/*`
包 —— 不会和 DSH 其他插件产生版本冲突。
