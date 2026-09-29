# DSH SkillMaster

> 为 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 创建、归档并**记忆** Agent Preset 会话。
> 仅用 Python 标准库，只连接本机 DSH，不发送任何遥测。

```bash
pip install dsh-conversation-studio        # 或直接 clone 本仓库使用
```

## 它解决什么

**问题一：新建的会话在侧栏看不见。**
DSH 把「还没有任何一轮对话」的会话标记为 `blank: true`，**Web 侧栏会隐藏它**。
`session/create` 明明返回成功、会话也确实存在，但你在界面里找不到。
本工具的 `create` 一站式命令会自动发一条首消息，让它立刻可见。

**问题二：预设有好几个，该用哪个？**
本工具从会话导出里读取 **DSH 发给模型的权威工具清单**，记住每个预设实际提供什么，
再结合你的使用习惯给出**可解释**的推荐：

```console
$ dshstudio recommend --want read,grep,web_search --avoid present,job_output
  review-focused   满足 4/4 项需求；你用过 1 次
  standard         满足 4/4 项需求，多出 2 项不需要的工具；你用过 3 次
  minimal          满足 0/4 项需求，缺少 ['glob', 'grep', 'read', 'web_search']
```

排序**先看功能匹配，用得多的只在打平时加分**——
一个你常用、但缺你明确要求的工具的预设，仍然是错误答案。

**问题三：想靠「问模型你有哪些工具」来判断预设能力。**
这条路不可靠。实测同一预设在自述中得到过 13 / 2 / 0 三种结果。
本工具改读会话归档里的 `request/header` 事件——那是宿主的真实视图。

## 装插件（可选，但推荐）

让本机 CLI 自行取得 token，**不必再从终端复制 URL**。把这段丢给你的 DSH 会话：

```
https://github.com/jijiwu3526/dsh-local-bridge
按它的 README 装这个 DSH 插件，并告诉我是否生效。
```

实际只执行一条命令，无需手改任何配置文件：

```bash
dsh plugin --profile web add github:jijiwu3526/dsh-local-bridge
# 然后重启 DSH
```

`dsh plugin add` 会装包、登记依赖，并因该包声明了 `dsh.bundle.patch`
而自动挂进层栈。**零运行时依赖**，只用 Node 内置模块。

细节与排障见 [`dsh_bridge/INSTALL.md`](dsh_bridge/INSTALL.md)。

## 三分钟上手

前提：DSH 已在运行，且下面二选一已经做好——

- **已装桥接插件**（推荐）：`dsh plugin --profile web add github:jijiwu3526/dsh-local-bridge`
  后重启 DSH。脚本会自己取得 token，**下面不用传任何 URL**。
- **未装插件**：把 DSH 启动时打印的完整 URL（含 `?token=`）设进环境变量：

  ```bash
  export DSH_WEB_URL='http://127.0.0.1:3080/?token=...'
  ```

```bash
# 1. 看有哪些预设
python3 dsh_session.py presets

# 2. 一站式建会话：创建 + 首发消息（否则侧栏看见）+ 记录这次选择
#    --workspace-id 必填，填 DSH 里已有工作区的 ID
#    （工作区在 DSH 侧栏点开后即可看到其标识）。
#    若手头没有现成工作区，改用 dsh_session.py new --workspace-path <目录>，
#    它会先把目录登记成工作区再建会话。
python3 -m dshstudio.cli create \
    --preset standard \
    --text "开工" \
    --workspace-id <你的工作区ID> \
    --archive records/s.zip \
    --scenario "日常开发"

# 3. 从导出里学习该预设的真实工具面
python3 -m dshstudio.cli observe records/s.zip --notes "日常开发"

# 4. 以后就能问它该用哪个预设
python3 -m dshstudio.cli recommend --want bash,read --avoid present
```

## 目录

| 路径 | 内容 |
| --- | --- |
| `dsh_session.py` | 会话创建、消息发送、日志导出（`presets` / `new` / `send` / `export`） |
| `dshstudio/` | 预设选型记忆（`observe` / `compare` / `recommend` / `habit` / `create`） |
| `docs/` | 设计、协议、留存方案与测试记录 |
| `docs/reviews/` | 独立的代码评审与预设边界测试报告 |
| `presets/` | 实际使用的预设配置留档 |
| `tools/` | 协议保真测试、故障注入与隔离性测试脚本 |
| `templates/` | 需求访谈、规格确认、Creator 施工指令 |
| `tests/` | 61 项自动化测试 |

## 已知边界

- **预设裁剪不到宿主注入的插件。** `mcp__computer_use__*` 这类插件由宿主 profile 注入，
  不在任何预设的权威清单里，却出现在所有会话中。要真正限制工具面，
  必须在宿主配置层处理，改预设无效。
- **会话无法通过 API 删除。** DSH 没有 `session/delete` 端点，误建的会话只能在 Web 侧栏处理。
- **空白会话在侧栏不可见**，因为 DSH 标记 `blank: true`。用 `send` 发一条即可。
- **推荐只覆盖已学习过的预设。** 先 `observe` 若干归档，候选才会变多。
- **记忆文件含本机绝对路径**（`~/.config/dsh-conversation-studio/memory.json`），
  分享前请处理。详见 [会话资料保存方案](docs/会话资料保存方案.md)。
- 面向 DSH 0.1.5-rc.1 验证。DSH 属开发预览，接口会变；
  升级后建议先按 [`docs/协议与版本.md`](docs/协议与版本.md) 核对。

## 开发

```bash
python3 -m unittest discover -s tests -v      # 61 项
export DSH_WEB_URL='http://127.0.0.1:3080/?token=...'
python3 tools/preset_robustness.py            # 故障注入（不消耗配额）
```

## 致谢与依据

协议形状以本机安装的 DSH 生成的 Typert 描述符为准逐项核对，
不依赖文档推测。核对记录见 [`docs/资料来源.md`](docs/资料来源.md)。

MIT 许可。


## 先用起来

1. 安装并启动 DSH Web，设置好模型：`dsh --profile web`。保留终端打印的完整启动 URL（新版带 `?token=...`）。
2. 在 DSH 中新建一个规划会话，复制 [`templates/01-需求访谈提示词.md`](templates/01-需求访谈提示词.md) 作为第一条消息，聊完后把确定的事实填写到 [`templates/02-工作用途与预设规格.md`](templates/02-工作用途与预设规格.md)。
3. 切换到 Creator（或“创造模式”）会话，把 [`templates/03-Creator制作指令.md`](templates/03-Creator制作指令.md) 连同已填写的规格发给它。让 Creator 使用当前 DSH 的官方 Preset/插件管理能力创建预设。到 **设置 → Agent 预设** 检查它健康可用，并点击 **设为默认**。
4. 在本项目目录运行：

   ```bash
   export DSH_WEB_URL='http://127.0.0.1:3080/?token=把DSH启动行里的实际值放在这里'
   python3 dsh_session.py presets
   python3 dsh_session.py new --workspace-path /绝对路径/你的项目 --register-workspace --preset 你的预设ID
   ```

   `--preset` 可以省略，此时 DSH 采用设置页选定的默认预设。首次认证后，Cookie 保存在本机 `~/.config/dsh-conversation-studio/cookies.txt`，后续命令会复用它；也可以把 `DSH_WEB_URL` 改成不带 token 的本机地址。若 Cookie 已失效，脚本会用你传入的 `?token=` 链接自动重新换一次 Cookie；只有在链接也不带 token（或同样失效）时才需要手动删除该缓存。创建结果会写进 `records/<session-id>.json`。

5. 在 Web 中找到新会话并发第一条正式任务消息。空白会话可能暂不显示在侧栏。聊完后保存原始完整日志：

   ```bash
   python3 dsh_session.py export session-实际ID --output records/正式工作会话.zip
   ```

脚本只调用本机运行的 DSH Web；`--workspace-path` 的目录必须已存在。仅 `--register-workspace` 会将目录登记成 Web 工作区；否则会话按 `cwd` 创建，可能归入未分组。可用 `--workspace-id` 直接指定已有工作区——它与 `--register-workspace` 互斥，同时给出会被明确拒绝。

## 命令

```bash
python3 dsh_session.py --help
python3 dsh_session.py presets
python3 dsh_session.py new --workspace-id <id> --preset <preset-id>
python3 dsh_session.py new --workspace-path /path/to/work --register-workspace
python3 dsh_session.py send <session-id> --text "消息" --wait 60
python3 dsh_session.py export <session-id> --output records/session.zip
```

脚本使用 Python 3.10+ 标准库，无需 `pip install`。它先尝试新版 `/api/session/create`、`/api/agentPresets/list`，仅当端点返回 404 时退回旧版点分协议。业务错误不会触发降级，也不会悄悄创建另一个会话。`--session-id` 可用于网络中断后的同 ID 重试，但先核对 DSH 中该 ID 的状态。

### 新建后必须发一条消息

DSH 把「还没有任何一轮对话」的会话标记为 `blank: true`，**Web 侧栏会隐藏这类会话**。
所以 `new` 之后必须发一条消息，它才会出现在侧栏里。这就是 `send` 存在的理由：

```bash
python3 dsh_session.py new --workspace-id <id> --preset <preset-id>   # 拿到 session_id
python3 dsh_session.py send <session-id> --text "开工" --wait 60      # 发一条，进入侧栏
```

`--wait` 会轮询到会话脱离 `blank` 状态为止，并在输出里报告标题与可见性。
不做这一步的话，会话在服务端确实存在、也能导出，但你**在 Web 里找不到它**。

## 预设选型记忆

`dshstudio` 记住每个预设**实际提供什么工具**、以及你**习惯用哪个**，用来回答
「这个任务该开哪个预设」。

```bash
# 一站式：建会话 + 首发消息 + 学习工具面 + 记录这次选择
python3 -m dshstudio.cli create --preset review-focused --text "开工" \
    --workspace-id <id> --archive records/s.zip --scenario "代码审阅"

# 单独使用
python3 -m dshstudio.cli observe records/s.zip --notes "适合只读审阅"
python3 -m dshstudio.cli compare standard review-focused
python3 -m dshstudio.cli recommend --want read,grep,web_search --avoid present,job_output
python3 -m dshstudio.cli habit --cwd /path/to/project
python3 -m dshstudio.cli incident review-focused "挂载失败：prefix missing"
```

记忆文件在 `~/.config/dsh-conversation-studio/memory.json`（0600，原子写入，
损坏时自动重建而非崩溃）。每条画像都记着来源归档，每条偏好都记着会话 ID，
推荐结果完全可解释、可纠正。

**工具面来自会话导出里的 `request/header` 事件**——那是 DSH 发给模型的权威
清单，不是模型自述。这一点很重要：靠「问模型你有哪些工具」会因为格式漂移而
不可靠，实测中它把 `minimal` 报成 13 个工具，而权威值是 1 个。

**推荐先按功能匹配排序，用得多的只在打平时加分**；习惯不会盖过你明确提出的
工具需求。DSH 内置但你没学习过的预设不会出现在候选里。

## 包内内容

| 文件 | 用途 |
| --- | --- |
| `dsh_session.py` | 本机认证、预设校验、会话创建、消息发送、原始日志导出 |
| `dshstudio/profile.py` | 从会话归档提取预设的权威工具面 |
| `dshstudio/memory.py` | 预设画像、使用偏好、故障记录的持久化与推荐 |
| `dshstudio/cli.py` | 记忆与一键建会话的命令行入口 |
| `templates/01-需求访谈提示词.md` | 规划会话中的问答流程 |
| `templates/02-工作用途与预设规格.md` | 用户确认的业务规则与预设清单 |
| `templates/03-Creator制作指令.md` | 交给 DSH Creator 的施工任务 |
| `docs/设计稿.md` | 流程、数据模型、边界与验收设计 |
| `docs/会话资料保存方案.md` | 聊天和配置的留存、脱敏与回溯 |
| `docs/协议与版本.md` | 新旧 Web 协议与 Preset 格式演进 |
| `docs/资料来源.md` | 上游依据与核对日期 |
| `tests/test_dsh_session.py` | 模拟两代 DSH Web 的协议测试 |
| `tests/test_memory.py` | 画像提取、记忆持久化与推荐排序测试 |
| `tests/test_bridge.py` | 桥接客户端的三级回退与传输失败降级测试 |
| `tests/test_packaging.py` | 打包元数据：本地 import 是否都被打进 wheel |

运行测试：`python3 -m unittest discover -s tests -v`（61 项）。

---

# 附：工具包原始文档

以下为本工具包随附的设计、协议与流程文档。

