# family_src — 冻结后的族谱核

一张「图纸 / 草图」在这条产线里是**族谱核**（一个 Python 模块），不是单张工程图。
`emit_members.py` 把族谱展开成数百个件号；每个件的 `build.py` 再调用核里的 `build(b, proj)`，
用该件自己的拓扑签名生成几何。核的字节不在生成过程中改。

本目录按机器分：

```
family_src/B/MANIFEST.json     每个 *.py 的 sha256 与总哈希
family_src/B/binding.json      族谱键 -> 核文件名，例如 in01 -> enclosure_core.py
family_src/B/enclosure_core.py 由 freeze 从生产机拷入（仓库里默认没有这些核）
```

生产机上的核今天在 `~/cad-fleet/family/`（B）或 A 线 cadRoot 的 `family/`，**没有进 git**。
云端改不了那些文件。冻结是主人在 Mac 上做的：

```sh
python3 tools/family_freeze.py freeze --machine B --src ~/cad-fleet/family
# 然后编辑 family_src/B/binding.json，把 in01.. 映到真实文件名
git add family_src/B && git commit
python3 tools/family_freeze.py check --machine B --live ~/cad-fleet/family
```

`check` 非 0 时不要开跑：活核和冻结核不是同一张图纸。
`fleet_runner` 在存在 MANIFEST 时会做同一次比对，不一致直接退出。
没有 MANIFEST 时允许跑（否则第一波永远开不了工），但验收报告把核标成 **UNFROZEN**，
这一波不能 ACCEPT。
