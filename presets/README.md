# 预设留档

本目录保存实际使用的 Agent Preset 配置，来源 `~/.dsh/.agent-presets/`。

**为什么要留档**：DSH 的预设 API 只有 `list` / `read` / `copy` / `deletePreset` / `select`，
**没有写入方法**。定制内容只能落盘到 `~/.dsh/.agent-presets/<id>/agent.cordis.yml`。
删掉该目录即永久丢失，且没有服务端副本可恢复。

| 文件 | 说明 |
| --- | --- |
| `review-focused/agent.cordis.yml` | 插件组合与 persona（以 `standard` 为基线，18 → 16 条目） |
| `review-focused/preset.yml` | 预设显示名与说明 |

## review-focused 的改动

在 `standard` 基础上：

- **移除 `present`** —— 渲染型产物不适合代码审阅
- **移除 `tool-jobs`** —— 审阅是「读—报」循环，不需要无人值守批处理
- **persona 追加审阅纪律** —— 要求以 `path:line` 佐证、区分事实与推断、未获明确要求不改文件

**保留** `delegation`（每簇派一个子代理）、`planning`（plan mode 硬阻断修改）、
`tool-web`（查上游文档），理由写在文件的注释里。

## 恢复方式

```bash
mkdir -p ~/.dsh/.agent-presets/review-focused
cp review-focused/*.yml ~/.dsh/.agent-presets/review-focused/
```

DSH 会在下次挂载时读取，无需重启。若要设为默认，仍需在设置页点击
（没有 API）。

## 已知约束

`@deepseek-ai/dsh-persona` 的 `prefix` 是**必填**的，即使自定义内容都写在
`suffix` 里也不能省。删掉会得到：

```
agent-presets: preset "review-focused" failed to mount:
  - $.prefix missing required value (at prefix)
```

## 与工具面的关系

本预设裁剪的是 DSH 自带工具链（28 → 24）。宿主 profile 注入的
`mcp__computer_use__*` 与 `mcp__node_repl__js` **裁剪不到**——要真正限制
工具面必须在宿主配置层处理。实测数据见 `../work/conversation-studio/PRESET-TEST-PLAN.md`。
