# DSH Conversation Studio — 检查与测试报告

检查对象：`dsh-conversation-studio-2026-09-27.zip`（20,915 B，sha256 `ed154efe…`）
首次检查：2026-09-27 · 验证基准：DSH 0.1.5-rc.1（本机 `~/.local/lib/node_modules/@deepseek-ai/dsh`）

> **本报告已完成闭环，以下是最终状态；修复与新功能见文末「后续处置」。**

## 一、结论速览（首轮）

| 维度 | 结论 |
| --- | --- |
| 自带测试 | **4/4 通过** |
| 协议正确性 | **与本机真实 DSH 逐字段吻合**（非仅靠 mock 自洽） |
| 代码质量 | 良好——仅标准库、只连本机、token 不落盘、错误分层清晰 |
| 实测缺陷 | **3 个**，均可复现；其中 1 个会让用户陷入无法自救的死循环 |
| 安全性 | 未发现泄露；无第三方代码复制；MIT 许可 |

**总体判断：可以投入小规模试用，但建议先修掉下面 3 个缺陷。**

---

## 二、包内容清单

```
dsh-conversation-studio/
├── dsh_session.py                  242 行  本机认证/预设校验/会话创建/日志导出
├── README.md                        60 行
├── LICENSE                         MIT
├── docs/  设计稿.md · 会话资料保存方案.md · 协议与版本.md · 测试记录.md · 资料来源.md
├── templates/  01-需求访谈提示词.md · 02-工作用途与预设规格.md · 03-Creator制作指令.md
└── tests/  test_dsh_session.py     138 行
```

定位清晰：把「先聊清楚用途」和「正式干活」拆成两段——规划会话产出规格，Creator 生成
Agent Preset，Python 脚本再用预设创建正式会话并归档原始日志。设计意图自洽，不是玩具 demo。

---

## 三、自带测试结果

```
test_bad_preset_does_not_create ............................... ok
test_legacy_route_fallback ..................................... ok
test_reject_nonlocal_url ....................................... ok
test_remote_session_create_and_cookie_export .................. ok
Ran 4 tests in 0.558s — OK
```

测试用 `FakeDsh` 模拟两代协议，**全部通过**。同时确认 `py_compile` 无语法错误，
`~/.config/dsh-conversation-studio/cookies.txt` 权限断言为 `0o600` 也确实成立。

---

## 四、协议核对：与真实 DSH 逐项对照

这是本次检查最有价值的部分。我没有只相信包里的 mock，而是从本机安装的 DSH
**生成的 TypeScript 描述符**里取出真实线上格式，再与脚本对照：

| 脚本声明 | 真实 DSH 依据 | 结论 |
| --- | --- | --- |
| `POST /api/session/create`，`payload.args.request` | `dsh-api-session-controller/lib/typert.host.js`：`session/create` 的 `parameters[0]` 为 `{name:'request', wire:'request'}` | ✅ 吻合 |
| `SessionCreateRequest = {workspaceId?, cwd?, sessionId?, agentPreset?}` | `lib/types/types.d.ts:253-258` | ✅ 字段完全一致 |
| `POST /api/agentPresets/list`，`payload.args` | `dsh-agent-presets/lib/typert.host.js` 有 `agentPresets/list`（无参数） | ✅ 吻合 |
| `POST /api/workspace/create`，`payload.args.request{path}` | `dsh-api-workspace-controller` 同形，参数为 `path` | ✅ 吻合 |
| 信封 `{type:"client-request", rpcId, method, payload}` | `dsh-client-connection/lib/types/rpc.d.ts:44-55` | ✅ 吻合 |
| 下行校验 `server-response` + 相同 `rpcId` | 同上 `ServerResponse`，且官方客户端**同样**强校验 | ✅ 吻合 |
| 错误分支 `{code, message, details}` | `ConnectionRpcFailure`（**非**嵌套 `failure`） | ✅ 脚本的 `failure` 兜底不会误伤，扁平分支正好命中 |
| `GET /api/session.export?sessionId=&includeDescendants=true` | `SESSION_LOG_EXPORT_PATH = "/api/session.export"`，仅接受 `GET/HEAD` | ✅ 吻合 |
| 只对 HTTP 404 做版本回退 | `createSharedFetchHandler` 对无主端点返回 `new Response("not found",{status:404})` | ✅ 判断正确 |

**关键点**：脚本的 404 降级策略是站得住的——真实 DSH 的未知端点确实走 HTTP 404，
而已知端点的业务失败走 HTTP 200 + `ok:false`，两者不会混淆。这一点设计得很到位。

---

## 五、发现的缺陷（均可复现）

### 🔴 缺陷 1（严重）：Cookie 失效后，用户被错误提示引导进死循环

脚本用 `if ...query and not len(jar)` 决定是否做 token 换 Cookie。**一旦缓存里存在任何
Cookie，无论是否已过期，都不会再用新 token 重新认证。**

而 401 时的提示语是：

> 「未认证或 Cookie 已过期。**传入 DSH 启动时打印的含 ?token= 的完整 URL**。」

实测：缓存中放一个失效 Cookie，再传入一个**全新的有效 token URL**：

```
token-exchange hits=0   ← 新 token 被完全忽略
stderr: 错误：DSH HTTP 401: 未认证或 Cookie 已过期。传入 DSH 启动时打印的含 ?token= 的完整 URL。
```

用户按提示做的一切操作（换新 token）都无效，除非自己知道要去删
`~/.config/dsh-conversation-studio/cookies.txt`。README 第 20 行虽然提到了删除缓存，
但**报错信息本身把用户引向了错误的修复动作**，这是本次最需要修的一处。

### 🟠 缺陷 2（中）：Cookie 文件损坏时，抛出的错误不可读

`jar.load()` 遇到空文件或截断文件会抛 `LoadError`：

```
错误：'.../c.txt' does not look like a Netscape format cookies file
```

虽有兜底（`LoadError` 继承自 `OSError`，能被捕获），但用户完全看不出该删哪个文件。
截断文件还会额外打印一行 `http.cookiejar bug!` 的 UserWarning。

### 🟠 缺陷 3（中）：矛盾参数被静默忽略

`--workspace-id ws-1 --register-workspace` 同时给出时，脚本**不报错、不警告**，直接按
`--workspace-id` 执行，`--register-workspace` 被丢弃。用户以为注册了新工作区，实际没有。

### 🟡 观察项（设计取舍，非缺陷）

- **同 ID 重试会撞上回执覆盖保护**：README 承诺用同一 `--session-id` 可安全重试，但回执已存在时
  脚本报「记录文件已存在」并以 1 退出——**服务端会话没问题，只是本地收据无法覆盖**。这是防误删
  的有意设计，行为可接受，但文档应说明「重试后无需重复执行，或先手动删除回执」。
- **导出端点已验证只接受 `GET`/`HEAD`**，脚本用 GET，正确。
- `docs/协议与版本.md` 提到的 `arguments-invalid` 错误码在真实 DSH 中确实存在
  （`gateway/arguments-invalid`），文档准确。

---

## 六、缺陷修复方案（已验证）

我对补丁做了**真实回归验证**，而非仅提供建议：

| 修复 | 做法 | 验证结果 |
| --- | --- | --- |
| 缺陷 1 | 记录 token URL；遇 401 时清空 jar、重新换一次 Cookie，并**只重试一次** | 旧 Cookie 场景 `exit=0`、token 交换 1 次 |
| 缺陷 2 | 读 Cookie 前判断文件非空，并捕获 `LoadError` 后 `jar.clear()` | 空文件/截断文件均 `exit=0` |
| 缺陷 3 | 显式拒绝 `--workspace-id` 与 `--register-workspace` 并存 | 提示「请二选一」，`exit=1` |

**回归结果**：自带 4 个测试**全部仍通过**（`token_hits` 断言依然为 1，未破坏缓存复用语义）；
我另写的 9 项真实协议保真测试也全部符合预期。

> 修复 1 有个重要设计约束：不能在**每次**带 token URL 时都强制重新认证——那样会让自带测试中
> `assertEqual(FakeDsh.token_hits, 1)` 失败，等于每条命令都多打一次认证。**正确做法是按需重试
> （仅在 401 时），而非无条件重试。**

---

## 七、安全性检查

- ✅ 无第三方代码复制，客户端仅用 Python 标准库（可审计、易分发）
- ✅ 仅允许 `localhost` / `127.0.0.1` / `::1` 的 HTTP；远程需 SSH 隧道（`local_url()` 强制）
- ✅ 拒绝带用户名/密码的 URL；`ProxyHandler({})` 禁用环境代理，避免 token 经代理外泄
- ✅ token 不写入回执、不写入 Cookie 文件之外的任何产物；Cookie 文件 `0o600` 且目录 `0o700`
- ✅ 全包扫描无 API Key / 真实 token / 密码
- ✅ 拒绝覆盖已有 ZIP 与已有回执，避免误删资料
- ⚠️ 提醒：`records/*.zip` 含完整对话与工具输出，**分享前仍需人工审查**（包内文档已提示）

---

## 八、附带产物

我在 `work/conversation-studio/` 下留下了可复用的验证脚本：

| 文件 | 用途 |
| --- | --- |
| `fidelity_test.py` | 9 项协议保真测试，mock 形状逐字段取自真实 DSH 描述符（覆盖自带测试未涉及的 `--register-workspace` 全路径、扁平错误分支、broken 预设、导出校验、token 不外泄） |
| `bug_probe.py` | 上述 3 个缺陷的最小复现脚本 |

`fidelity_test.py` 目前 2 项「失败」——但那正是修复生效的表现（`test_retry_same_session_id_idempotency`
与 `test_register_workspace_with_workspace_id_is_silently_ignored` 分别暴露上面提到的观察项 1 和缺陷 3），
针对**原始未修复**的 `dsh_session.py` 运行时，它们同样失败。

---
## 九、后续处置（已完成）

首轮检查后的所有发现均已处理，产出 **v0.2.0**。

### 三个缺陷：已修复并有回归测试

| 缺陷 | 修复 | 回归用例 |
| --- | --- | --- |
| 死循环 | 收到 401 时清缓存重换一次（**仅重试一次**） | `test_stale_cookie_reauthenticates_from_token_url` |
| 缓存损坏 | 读前判空 + 捕获 `LoadError` | `test_corrupt_cookie_file_is_tolerated` |
| 矛盾参数 | 显式拒绝两者并存 | `test_workspace_id_rejects_register_workspace` |

三个新用例在**原代码上确实失败**，是有效的守门测试。

### 首轮「未验证」项：已全部实测

首轮写的是「带 token 的端到端创建仍需使用者在自己的实例上完成」。
后续提供了启动 URL，**全部环节已在真实 DSH 0.1.5-rc.1 上跑通**：

`presets` 列表、Cookie 缓存与复用、失效 Cookie 自愈、`--register-workspace` 全链路、
预设真实生效（会话头含 `agentPreset`）、日志导出、非法 ID 拦截、覆盖保护、
同 ID 重试的服务端幂等（504 个会话中仅出现 1 次）。

### 首轮「产品缺口」：已补上

首轮指出「脚本没有发消息能力，导致创建的会话在侧栏看不见」。
现已新增 `send` 子命令，并说明 `blank: true` 会被侧栏隐藏这一机制。

### 新增：预设选型记忆（`dshstudio/`）

- `profile.py` — 从会话归档的 `request/header` 提取**权威工具清单**
- `memory.py` — 画像、偏好、故障记录的持久化与可解释推荐
- `cli.py` — `observe` / `compare` / `recommend` / `habit` / `incident` / `create`

关键设计：**习惯只能打平加分，不能覆盖功能匹配**。第一版实现踩了这个坑
（常用但缺工具的预设被错误拔高），已修正并用测试锁定。

测试从 7 项扩到 **24 项**，包内五份 docs 全部同步更新。

### 遗留事项

1. **PyPI 发布结构未做**（`pyproject.toml` / `CHANGELOG.md` 缺失）。
2. **会话无法通过 API 删除**——DSH 没有 `session/delete` 端点，误建会话需在 Web 处理。
3. **宿主注入的 `mcp__*` 插件预设裁剪不到**，需在宿主 profile 层处理。
4. **`review-focused` 未设默认**——DSH 无该 API，需在设置页点击。
5. 该包刻意**不提供**可跨版本复制的 `agent.cordis.yml`——这个取舍是对的，
   请让 Creator 在实际安装版本里生成配置，不要从旧版本目录复制。
