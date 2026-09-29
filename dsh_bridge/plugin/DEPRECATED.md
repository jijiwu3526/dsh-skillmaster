# 已废弃

本目录是 `dsh-local-bridge` 插件的**早期副本**，保留仅为历史参考。

插件已独立成仓库：**<https://github.com/jijiwu3526/dsh-local-bridge>**

请勿按本目录安装。原先的安装方式：

```bash
dsh plugin --profile web add github:jijiwu3526/dsh-skillmaster#subdir=dsh_bridge/plugin
```

在本机 pnpm 10.33.4 上**实测失败**：

```
ERROR  Could not resolve subdir=dsh_bridge/plugin to a commit of
       git@github.com:jijiwu3526/dsh-skillmaster.git.
```

`#subdir=` 语法不被该版本支持，且官方插件（如 `@hytime/dsh-thinking-effort`）
本身也都是独立仓库——所以改为独立发布，与 DSH 的惯例一致。

当前安装方式见 [`../INSTALL.md`](../INSTALL.md)。
