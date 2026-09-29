# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.0] — 2026-09-29

### 新增

- **桥接客户端测试**（`tests/test_bridge.py`）—— `dsh_bridge.py` 此前**零覆盖**。
  新测试锁定三级回退链（插件 → `DSH_WEB_URL` → 裸 origin）、空密钥文件、
  403/404/500 的错误文案、「凭据绝不经过环境代理」，以及下述六种
  传输层失败都必须降级而不是崩掉。
- **CI**（`.github/workflows/ci.yml`）—— Python 3.10/3.11/3.12/3.13 跑 `unittest`；
  另有 secret 扫描（token 字面量、本机绝对路径、误提交的运行产物）与
  「已废弃的插件副本必须与上游逐字节一致」检查。插件仓库另有自己的 CI。
- **插件测试**（`dsh-local-bridge` 仓库的 `test/index.test.js`，28 项）——
  此前的插件代码没有任何测试。新测试用 mock ctx 确认三道防护各自真的会挡。
- `dsh_bridge/plugin/DEPRECATED.md` —— 标记包内的旧插件副本仅供历史参考。

### 修复

- **桥接不可用时会崩掉 CLI，而不是回退**。`fetch_via_bridge` 原先只捕获
  `HTTPError` 与 `URLError`，实测有**六种**异常会穿透 `resolve()`，在命令行上
  留下原始 traceback——而这恰恰是该静默降级的场合：

  | 异常 | 触发场景 |
  | --- | --- |
  | `TimeoutError` | DSH 正在启动或卡住（**最可能的一种**） |
  | `IncompleteRead` | body 短于 `Content-Length`，连接中途断 |
  | `BadStatusLine` | 端口上跑的是别的进程，返回的不是 HTTP |
  | `ConnectionResetError` | 对方在发状态行前 RST |
  | `KeyError: 'url'` | 返回 `{"ok": true}` 却没有 `url` |
  | `ValueError: unknown url type` | origin 没写 scheme |

  现按异常族分别捕获，每种都带上可操作的提示（「DSH 正在重启？」、
  「是否还在启动？」），并对缺失/非字符串的 `url` 显式报错。
  另新增 `_normalise_origin()`，让 `localhost:3080` 这种写法也能用。
- **`DSH_HOME` 被客户端忽略**：插件优先读 `$DSH_HOME`，客户端却写死
  `~/.dsh`。自定义过 home 的用户永远找不到密钥，只会看到一个费解的
  「未找到共享密钥」。现两端一致。
- **`dsh-local-bridge` 插件此前根本无法加载**（发在独立仓库，0.1.0 → 0.1.1）。
  源码在任何 Node 上都会在 import 阶段抛 `SyntaxError`，也就是说按文档
  执行 `dsh plugin add` 的人装上的是一个不工作的插件。三个致命问题：
  - `randomBytes` 从 `node:fs` 导入（它属于 `node:crypto`）。
    `node --check` **发现不了**——它只做语法解析，不解析 ESM 具名导出。
    这正是此前一路「验证通过」却没暴露的原因。
  - `inject` 里服务名写成 `webserver`，宿主实际是 `webServer`。
  - `ctx.effect(() => unlinkSync(path))` 用法错误：`ctx.effect` 要求回调
    **返回** disposer，原写法会在启动时就删掉密钥文件且什么都没注册，
    密钥因此活过进程生命周期。
  - `SECRET_PATH` 在模块加载时求值，冻结了当时的 `DSH_HOME`；已改为惰性求值。

  本机已安装的那份是修正过的，所以此前一直在正常使用——**问题只存在于
  GitHub 上那份**，任何新装的人都会中招。详见插件仓库的 CHANGELOG。
- **类型声明指向了错误路径**：`dsh_bridge/plugin/index.d.ts` 里 `BRIDGE_PATH`
  的注释仍写「on the shared `/api` channel」，`cordis.patch.yml` 注释写的则是
  `/api/local-bridge/auth`——**与实际代码 `BRIDGE_PATH = "/local-bridge/auth"`
  直接矛盾**，会引导使用者在 `/api` 下挂载，重蹈 401 的老路。
  现两处都写明「故意不在 `/api` 下」及原因。
- **桥接返回非 JSON 时会崩掉 CLI**：反向代理的 HTML 错误页、或 DSH 重启到
  一半写出的响应，会让 `json.load()` 抛的 `JSONDecodeError`（一个
  `ValueError`）穿透 `resolve()`。此项已包含在上一条的全面修复中。
- **`pyproject.toml` 声明了不存在的文件**：`package-data` 写了
  `dshstudio = ["py.typed"]`，但该文件不在仓库里，打 wheel 会告警并静默丢弃。
  现已补上。
- **文档里的安装命令是失效的**：`#subdir=dsh_bridge/plugin` 语法在本机
  pnpm 10.33.4 上**实测失败**（`Could not resolve subdir=...`）。
  README 与 `INSTALL.md` 已统一指向独立仓库
  [`dsh-local-bridge`](https://github.com/jijiwu3526/dsh-local-bridge)。
- README 里重复的「已知边界」章节（拼接仓库首页与包内文档时留下的）。

### 文档

- CHANGELOG 补记 0.2.0 之后新增的插件与桥接客户端。
- 全仓库扫描确认无 token、无本机绝对路径泄漏。

## [0.2.0] — 2026-09-27

### 新增

- **`dshstudio` 记忆模块** —— 记住每个 Agent Preset 实际提供哪些工具，以及你习惯用哪个，
  用于回答「这个任务该开哪个预设」。
  - `profile.py`：从会话导出的 `request/header` 事件提取**权威工具清单**。这是 DSH
    发给模型的真实清单，比「问模型你有哪些工具」可靠得多——后者会因格式漂移失真。
  - `memory.py`：预设画像、使用偏好、故障记录的持久化与可解释推荐。
  - `cli.py`：`observe` / `compare` / `recommend` / `habit` / `incident` / `create`。
- **`dsh_session.py send` 子命令** —— 新建会话后发一条消息。
  DSH 把「没有任何一轮对话」的会话标记为 `blank: true` 并**在侧栏隐藏**，
  不发消息就找不到刚建的会话。`--wait` 会轮询到脱离该状态并报告标题。
- `presets/` —— 实际使用的预设配置留档（DSH 的 preset API 没有写入方法，
  `~/.dsh/` 下的配置删掉即永久丢失）。

### 修复

- **认证死循环**：本机缓存中只要存在任何 Cookie（无论是否过期），脚本就不再用启动 URL
  里的 token 重新认证，而错误提示却建议「传入含 `?token=` 的完整 URL」——该建议在此
  情况下无效。现在收到 401 时会清空缓存并重新交换一次 token（**只重试一次**，
  不影响正常缓存复用）。
- **损坏的 Cookie 缓存**：空文件或截断文件原本抛出不可读的
  `does not look like a Netscape format cookies file`。现在会丢弃并重新认证。
- **矛盾参数**：`--workspace-id` 与 `--register-workspace` 同时给出时原本静默忽略后者，
  现在明确报错。

### 文档

- 记录 `blank` 机制、预设 API 只读、`broken` 字段排障、权威工具清单的来源，
  以及一条实测得出的边界：**预设裁剪不到宿主 profile 注入的插件**
  （如 `mcp__computer_use__*`），要限制工具面必须在宿主配置层处理。

### 测试

7 → 24 项。三个修复各配一条回归用例，记忆模块 17 项。

## [0.1.0] — 2026-09-27

初始版本：基于 DeepSeek Harness 本机 Web RPC 创建与归档会话。
仅用 Python 标准库，支持新旧两代协议，仅连接本机地址。

### 首轮评审发现（已全部修复，详见 `docs/reviews/REVIEW.md`）

1. 认证死循环（见 0.2.0）
2. 缓存损坏报错不可读（见 0.2.0）
3. 矛盾参数静默忽略（见 0.2.0）
4. 产品缺口：无法发送消息，导致创建的会话在侧栏不可见（见 0.2.0 `send`）
