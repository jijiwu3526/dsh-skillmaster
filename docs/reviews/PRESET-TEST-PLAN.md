# DSH Agent Preset 边界与鲁棒性测试计划

日期：2026-09-27 · 环境：DSH 0.1.5-rc.1（本机 `127.0.0.1:3080`）· 预设：`review-focused`

> **修订说明（10:06 后补充）**：本文件初版 A 节的数据来自「问模型你有哪些工具」，
> 后续在会话导出的 `request/header` 事件中找到了**权威来源**。下表已替换为权威值。
> 原自述值一并保留，用于说明该方法的不可靠程度。

## 为什么需要这套测试

包内自带的 mock 测试只能验证 **HTTP 形状**。它发现不了真实故障——本次实测中，
「删除 persona 的必填 `prefix`」这个错误 mock 永远看不见，只有真实 DSH 才会报
`agent-preset/invalid`。下面三类问题都必须在真实实例上验证。

## A. 边界测试：预设到底能控制什么

**方法**：三个预设各建一个会话并导出归档，从 `request/header` 事件的
`data.header.tools` 读取 **DSH 实际发给模型的工具清单**。

### 权威数据

| 工具 | minimal | standard | review-focused |
| --- | :---: | :---: | :---: |
| `read` `write` `edit` `grep` `glob` | — | ✓ | ✓ |
| `bash` | — | ✓ | ✓ |
| `present` | — | ✓ | **—** |
| `job_output` `job_kill` `job_list` | — | ✓ | **—** |
| `subagent` `workflow` `web_search` | — | ✓ | ✓ |
| `mcp__computer_use__*`（宿主） | — | — | — |
| `mcp__node_repl__js`（宿主） | — | — | — |
| **合计** | **1** | **28** | **24** |

### 为什么自述法不可靠

| 预设 | 权威工具数 | 模型自述 | 说明 |
| --- | --- | --- | --- |
| `minimal` | **1** | 13 | 宿主注入插件被算进来了 |
| `standard` | **28** | 40 | 同上 |
| `review-focused` | **24** | 36 | 同上 |

同一预设在自述中三次测量得到过 **13 / 2 / 0** 三种结果（格式漂移：反引号 vs 列表项、
复问 vs 首答、最长回复 vs 工具最多）。基于自述的任何断言都不可靠。

### 三条结论

1. **预设能裁剪 DSH 自带工具链** —— `present` 与 `job_*` 在 `review-focused` 中
   确实消失，28 → 24。`minimal` 只剩 `bash` 一个工具。
2. **预设管不到宿主注入的插件** —— `mcp__computer_use__*` 和 `mcp__node_repl__js`
   **不出现在任何预设的权威清单里**，但会话中实际可用。它们由宿主 profile 注入
   （不在 DSH 的 `node_modules/@deepseek-ai/` 内）。
   **要真正锁死工具面，必须在宿主 profile 层禁用插件，改预设无效。**
3. **`minimal` 名义单工具、实际可用十几个** —— 它只有 `persona` + `persistent-shell`
   两个条目，其余全是宿主注入。

## B. 故障注入：预设坏了会怎样

**方法**：复制 `review-focused` 为临时预设 `rb-test`，逐个注入 7 类缺陷，
每次都尝试建会话，并检查 roster 是否被拖垮。

| 注入的缺陷 | DSH 行为 | 拦截层 | 结果 |
| --- | --- | --- | --- |
| 缺必填 `prefix` | 报 `$.prefix missing required value` | **session/create** | ✅ |
| 未知插件包名 | roster 标记 `broken`，报「预设当前不可用」 | **脚本预检** | ✅ |
| YAML 语法错误 | roster 标记 `broken`（`must be a top-level list of plugin rows`） | **脚本预检** | ✅ |
| 重复 `- id:` | 拒绝 | session/create | ✅ |
| 空文件 | roster 标记 `broken` | **脚本预检** | ✅ |
| 群组缺 `isolate` | 拒绝 | session/create | ✅ |
| `preset.yml` 名字含空格 | **接受** | — | ⚠️ 非缺陷 |

### 最有价值的发现：脚本有两道防线

包内脚本的设计（`dsh_session.py` 的 broken 预检）**确实有效**，而且比 README 描述的更精细：

- **第一道（本地预检）**：roster 条目里 `broken` 是字符串型原因，脚本在
  `session/create` **之前**就拦截，请求根本没发出去。实测 `broken` 值为
  `"the composition must be a top-level list of plugin rows"`。
- **第二道（服务端）**：预检漏过的（如缺 `prefix`）由 DSH 在挂载时报错，
  错误信息带**文件路径 + 字段名 + 原因**，可定位。

两类失败的错误文本完全不同 —— 前者笼统（"先查看 DSH 的配置诊断"），
后者精确。**排障时应先看 roster 的 `broken` 字段拿第一手原因。**

## C. 恢复性：坏预设会不会拖垮 DSH

| 检查 | 结果 |
| --- | --- |
| 连续 6 次故障注入后，roster 是否仍完整 | ✅ 始终 5–6 个预设，无丢失 |
| 故障期间 `standard` 能否正常建会话 | ✅ 每次都成功 |
| 删除坏预设后是否恢复 | ✅ roster 回到 5 个 |
| 其他预设是否被污染 | ✅ `review-focused` 内容逐字节一致 |

**结论：预设故障是会话局部的，不会污染进程。** 这与 `agent.cordis.yml` 头注释里
「service row 必须待在带 `isolate` realm 的群组内」的设计意图一致。

## D. 隔离性：预设之间会串味吗

| 检查 | 结果 |
| --- | --- |
| 两个会话的 `agentPreset` 字段 | 各自独立，未互相改写 |
| 工具集差异 | 14 项，无交叉污染 |
| persona 文本 | 各会话独立，`review-focused` 复问仍为审阅模式 |
| 已加入预设的会话是否受后续改动影响 | 不受影响（会话锁定创建时的预设） |

## 已发现的真实缺陷（不属于故障注入，是实操踩到的）

**`@deepseek-ai/dsh-persona` 的 `prefix` 是必填的。**

创建 `review-focused` 时我把 persona 的 `prefix` 删掉、只保留 `suffix`，
以为自定义内容都写在 `suffix` 就不需要它。真实 DSH 拒绝挂载：

```
agent-presets: preset "review-focused" failed to mount:
failed to apply loader entry persona (@deepseek-ai/dsh-persona): invalid config:
  - $.prefix missing required value (at prefix)
  (~/.dsh/.agent-presets/review-focused/agent.cordis.yml)
```

恢复 `prefix` 后一次通过。**这类错误只有真实 DSH 能发现**——mock 只验证 HTTP 形状。
已写入预设文件的注释，防止后续维护者重蹈覆辙。

## 复现方式

```bash
export DSH_WEB_URL='http://127.0.0.1:3080/?token=<启动时打印的值>'
python3 preset_robustness.py   # B + C：故障注入与恢复
python3 preset_isolation.py    # A + D：边界与隔离（会真实消耗 token）
```

两个脚本只操作 `~/.dsh/.agent-presets/rb-test/` 这一个临时预设，结束时删除。
**不要对正在使用的预设跑故障注入。**

> 工具面数据现在可以直接用 `python3 -m dshstudio.cli observe <会话导出.zip>` 读取，
> 不需要再跑 `preset_isolation.py`（那条路径会消耗模型配额）。

## 已知测试局限

1. **未测试**：并发创建、预设热重载、大规模预设数量下的 roster 性能。
2. **`session/prompt` 的 `steer` 模式**未实测。
3. **`preset.yml` 名字含空格被接受**——复查后确认 DSH 只把它当显示名，非缺陷，
   原测试用例设计有误，已在结论中标注。
4. **~~工具集靠模型自述~~** —— 已解决。改用 `request/header` 权威清单，
   该局限不再成立。
