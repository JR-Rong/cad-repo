# 插件源码 `@local/dsh-plugin-cad-freecad` 0.5.0

这里是 CAD 流水线插件的**源码**，随仓库存档，目的是：判重指纹函数（`python/cadkit/fingerprint.py`）、
交付规格（`python/cadkit/contract.py`）、台账口径与 `handoff/tools/merge_check.py` 里的门禁**永远同源可查**。

装机请用打好的一键包：[`../releases/local-dsh-plugin-cad-freecad-0.5.0.tgz`](../releases/local-dsh-plugin-cad-freecad-0.5.0.tgz)
（sha256 记在 [`../shared/versions.json`](../shared/versions.json)，`merge_check` 会核对两边是否同一个包）。

```sh
# 外来机器（推荐：直接装 tgz）
dsh plugin --profile desktop add releases/local-dsh-plugin-cad-freecad-0.5.0.tgz
# 完全退出并重启 DSH（侧栏图标只有重启后才出现：Host 缓存 dsh.client 扫描结果）

# 要改源码再装（A 机做的事）：改完重新打包，sha 变了就重签 versions.json 再 push
cd plugin/dsh-plugin-cad-freecad && npm pack --destination ../../releases/
sha256sum ../../releases/*.tgz
```

- `python/cadkit/` 是全部工程口径（校验/探针/导出/打包/判重）；`index.js` 只是 DSH 侧的动作路由与内存护栏。
- `lib/client.js` + `dsh.client` 是侧栏面板（内存预算、当前阶段、交付清单完成度）。
- 目录里**不含任何机器专属路径**：`freecadcmd` 自动发现，cadRoot 走配置并支持 `~` 展开，
  每次调用都会返回实际使用的 `cad_root` / `freecad_bin` 及其来源，排错不用猜。
- `test/*.test.mjs` 需要 DSH 主仓依赖，只在 A 机跑；外来机器不必跑。
