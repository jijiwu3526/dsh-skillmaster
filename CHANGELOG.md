# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
