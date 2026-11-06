# fcitx5-ppa 维护指南

自动化 GitHub Action, 每天从 Debian sid 拉取 fcitx5 相关源码包, 重打 `~<series>1~ppaN` 后缀, 上传 Launchpad PPA `ppa:ayanamist/fcitx5` 供 Ubuntu 24.04 (noble) 和 26.04 (resolute) 使用.

## 追踪的包 (9 个)

`fcitx5`, `fcitx5-chinese-addons`, `fcitx5-gtk`, `fcitx5-lua`, `fcitx5-qt`, `fcitx5-rime`, `libime`, `librime`, `yoga`

## 依赖图 (源自 Debian sid `debian/control` Build-Depends)

```
Stage 1 (leaves, 无 PPA 内依赖):  librime, yoga, fcitx5-gtk
Stage 2 (需 yoga):                fcitx5
Stage 3 (需 fcitx5[+librime]):    libime, fcitx5-lua, fcitx5-qt, fcitx5-rime
Stage 4 (需 fcitx5+libime+lua+qt): fcitx5-chinese-addons
```

依赖真相由 `scripts/deps.map` 记录；`scripts/pin-build-deps.py` 按同次 run 的上游版本提高源码 Build-Depends，pbuilder A-hook 使用同一下限校验实际安装版本。Debian upstream 若改 Build-Depends 必须人工更新此表，并更新九包元数据样本验证映射完整性；见 [样本说明](tests/fixtures/debian-sid/README.md)。

**已知细节**:
- `fcitx5-gtk` 只依赖 gtk/glib/x11, 不依赖 fcitx5 本体, 属 leaf
- `fcitx5-chinese-addons` 分裂依赖 `libimecore/pinyin/table-dev` 三个二进制包, 都来自 `libime` 源码
- `libfcitx5-qt6-dev` 命名可能随 Qt 版本变化 (Qt5 时代是 `libfcitx5-qt5-dev`), Debian 换 Qt 时需改 deps.map
- `fcitx5-modules-dev` 与 `fcitx5-module-lua-dev` 都来自 `fcitx5` 源码

## 版本策略

Debian sid 版本 `X` → PPA 版本 `X~<series>1~ppa1` (series 为 noble 或 resolute). 相同前缀已存在则 `~ppaN+1` 递增. PPA 版本 ≥ 期望首个 `~ppa1` 则 skip (说明已发过或 upstream 未变). 不需为 "依赖版本变了" 主动 bump — 相信 Debian maintainer 会 bump upstream, 自动流程只做 rebuild.

## 构建流程 (per package)

1. `apt-get source <pkg>` 拉 sid 源码 (workflow 在 apt sources 加 `deb-src http://deb.debian.org/debian sid main` + apt pin `-1` 阻止拉 Debian 二进制)
2. `dpkg-parsechangelog -SVersion` 拿 Debian 版本
3. Launchpad REST API 查 PPA 现有版本, 计算 `~ppaN`
4. 应用公共/发行版 patch 后，`pin-build-deps.py` 提高内部构建依赖下限；`dch` 生成 changelog 条目，`debuild -S -sa -k$GPG_KEY_ID` 生成签名源码包
5. **pbuilder 本地测试编译** (含 A-hook 依赖版本校验), 缓存 chroot 到 `actions/cache@v4` (key 含 run_id 强制每天刷新, restore-key 前缀匹配复用)
6. `dput ppa:$OWNER/$PPA` 上传
7. 将本次 run 的 deb artifact 交给后续 stage，建立本地 apt repo；下游编译无需等 Launchpad 发布。

`build-ppa.yml` 的 series matrix 使用 `max-parallel: 2`、`fail-fast: false`，两套发行版同时执行各自的 `build-series.yml` 四阶段 needs 链。`build-stage.yml` 的 build matrix 并行构建同阶段所有包，构建任务不持上传锁。阶段全部构建成功后，其单独的 upload job 按 package 列表顺序上传；下一阶段依赖整个前一阶段调用完成。

**上传锁不变式**：入口 `concurrency: ppa-upload` 保持不同 run 串行；各发行版始终只有一个活动阶段，故最多两个 stage upload job 争用 `ppa-transfer-<owner>-<ppa>` 锁，一个运行、一个等待，不会出现第三个 pending job 替换旧任务。锁只挂在 upload job，并与入口 group 不同。增加发行版、并发 run 或改变阶段依赖时，必须重新核对队列容量，并同步更新依赖链回归测试。

**源码交接**：build job 将签名 `.changes`、`.dsc`、完整 orig/component tarball 和公钥打包为 source artifact。缓存命中或仅补缓存也必须生成显式 no-upload 元数据，stage 协调器要求每个包均有记录。upload job 校验 package、series、source version、文件大小和 SHA256，取得锁后再次查询 PPA，避免排队期间版本已存在而重复上传。私钥不进入 artifact。

## 依赖版本一致性校验 (关键)

**问题**: pbuilder 装 build-dep 时可能因 apt cache/mirror 时序拿到旧版 `-dev` header, 编出的二进制 ABI 与 PPA 上传的新库不匹配.

**方案**: 签名前按 `deps.map` 从 `LOCAL_REPO_DIR/Packages` 读取同次 run 交接的版本，提高源码 `Build-Depends{,-Arch,-Indep}` 下限。动态下限仅取上游版本（如 `1.1.17`），去掉 Debian revision 和 PPA 后缀，保留 epoch、预发布和 repack 标识；上游已声明的更严格要求继续保留。版本记录到临时 JSON，pbuilder A-hook 在安装 build-dep 后读取相同下限，通过 `dpkg-query` 和 `dpkg --compare-versions` 校验，不足即 fail，不上传。Launchpad 无法再使用旧上游版本通过依赖检查，但可使用相同上游版本的其他打包修订。

**注意**: **必须 A-hook 不能 D-hook**. D-hook 在 build-dep 装**之前**触发, dpkg-query 全空 → 全部 skip, 校验形同虚设.

**跳过场景**:
- `deps.map` 条目已不在 Build-Depends 中 → warning 跳过该条目，维护时同步修表
- 架构/profile 限制使依赖未安装 (`dpkg-query` 空) → A-hook warning 跳过
- 已声明的内部依赖缺少本地 repo、必要二进制或来源/series 不匹配 → 签名前失败；首次 bootstrap 同样使用本次 run 的产物，不依赖 PPA 已发布

每次新准备源码动态提高下限，已有活动版本继续按版本幂等规则复用。验证运行 `bash scripts/test.sh`；`tests/test_pin_build_deps.py` 覆盖九包内部依赖、真实 `.dsc` 生成和旧版本拒绝，样本来源见 [样本说明](tests/fixtures/debian-sid/README.md)。

**故障背景（2026-11-06 核实）**：[Actions run 42039464880](https://github.com/ayanamist/fcitx5-ppa/actions/runs/42039464880) 的 chinese-addons 5.1.15 使用本地产物 libime 1.1.17 编译成功，而 [Launchpad build 33916331](https://launchpad.net/~ayanamist/+archive/ubuntu/fcitx5/+build/33916331) 使用满足旧依赖声明的 1.1.14，缺少 `HistoryBigram::WordWithCode` 导致失败。源码 publication 的 `Published`、编译日志的成功均不能证明二进制已发布；当前 Actions 以本地验证和源码上传结果判定成功，未等待 Launchpad 最终构建结果。

## 发行版适配 patch

**两类 patch**:

**`patches/{common,<series>}/<pkg>/`** — quilt 格式，改**上游源码文件**（orig.tar.xz 里的文件，如 CMakeLists.txt）。build-and-upload.sh 复制到 `debian/patches/` 并追加 `series`，dpkg-source `--before-build` 钩子 apply。

**`debian-patches/{common,<series>}/<pkg>/`** — 标准 unified diff，改 **`debian/` 目录下的文件**（debian.tar.xz 里的文件，如 debian/control）。build-and-upload.sh 在 debuild 前直接 `patch -p1` apply。**不能加入 quilt series**：dpkg-source -b 验证时从 orig 重跑，orig 里无 debian/，会报 "Reversed or previously applied"。

**判断"API 兼容"**: 看 upstream changelog / SONAME 变化. 若 SONAME 未变，通常 header ABI 兼容. 编译失败说明真断 API，得升级 noble 的 lib 或换其他策略.

**选择顺序**：先 `common`，再目标 `SERIES`；各目录内按文件名排序。公共 patch 放 `common/<pkg>/`，Noble 专用 patch 放 `noble/<pkg>/`。新增或调整发行版时，先查 [README 的发行版与 patch](README.md#发行版与-patch) 中的版本证据、适用范围和复核命令，再决定保留哪些 patch。

**当前 patch**:
- `debian-patches/common/{fcitx5,fcitx5-chinese-addons,fcitx5-lua}/debhelper-compat.patch`：两套发行版共享，compat 14→13，同时给全部 arch-any 包显式补 `${shlibs:Depends}, ${misc:Depends}`。
- `patches/noble/fcitx5/relax-wayland-protocols-version.patch`：WaylandProtocols 1.46→1.45，使用内置 Plasma XML。
- `debian-patches/noble/fcitx5/noble-compat.patch`：移除 Plasma 系统协议依赖。
- `debian-patches/noble/fcitx5/use-gcc14.patch`：规避 Noble GCC 13 编译 coroutine 的崩溃。
- `debian-patches/noble/librime/noble-compat.patch`：放宽四项依赖版本约束。
- `debian-patches/noble/fcitx5-chinese-addons/noble-compat.patch`：放宽 OpenCC 版本约束。
- Resolute 目前只需公共 patch；保留原始 Wayland/Plasma/OpenCC 等依赖要求和默认编译器。

**⚠️ debhelper compat 14 陷阱**：debhelper compat 14 新增特性：`dh_gencontrol` 自动将 `${shlibs:Depends}` 和 `${misc:Depends}` 注入所有 binary package 的依赖字段，即使 `debian/control` 里没有显式写出。Debian upstream（fcitx5/fcitx5-chinese-addons/fcitx5-lua）已依赖此特性，`debian/control` 里的库包 stanza 完全省略了 `Depends` 字段。Noble 和 Resolute 的 debhelper 均未提供 compat 14 虚包，**必须降到 compat 13**，但降级后自动注入失效，导致 `libfcitx5core7` 等包的 `Depends` 为空（无 `libxkbcommon0`、无 `libc6`），下游包 pbuilder 构建时找不到 `.so` 而 link 失败。**修法**：凡是降 compat 14→13 的包，必须在 `debhelper-compat.patch` 里给全部 arch-any binary package 手动补上 `${shlibs:Depends}, ${misc:Depends}`。**Debian upstream 新增包时也要同步更新 patch。**

**新增 patch 流程**:
1. pbuilder log 里 `pbuilder-satisfydepends-dummy` 失败 → `debian-patches/`；cmake 报 `Could NOT find X >= Y` → `patches/`
2. 确认 noble 已有版本 < 要求但 API 兼容
3. 拉原始文件，改，`diff -u` 生成 patch，`patch -p1 --dry-run` 验证
4. 重跑 workflow

## GPG / Launchpad key 处理

**上传签名**: 用户私钥 (Repository secrets: `GPG_KEY_ID`/`GPG_PRIVATE_KEY`/`GPG_PASSPHRASE`) 由 composite action 导入. loopback pinentry + `DEBSIGN_PROGRAM` wrapper 注入 passphrase 给 `debsign`.

**pbuilder 拉 PPA (验证签名)**:
- 运行时从 Launchpad API 拿 PPA `signing_key_fingerprint`
- 从 `keyserver.ubuntu.com` 下载 dearmor 到 `/etc/apt/keyrings/ppa-<owner>-<ppa>.gpg`
- pbuilderrc 用 `deb [signed-by=...]` + `BINDMOUNTS` 让 chroot 内可见 keyring
- **首次空 PPA 无 signing key** 降级 `ALLOWUNTRUSTED=yes` 完成 bootstrap, 之后自动切换正常鉴权
- **已有 key 但目标 series 无索引**：检查该 series 的 Release，只有明确 404 时省略 PPA mirror；create/update 均只用 Ubuntu 和本次 run 的 local repo。200 恢复 PPA，其他 HTTP/网络错误阻断；见 `scripts/check-ppa-series.sh`。

## GitHub 配置

**Repository secrets** (Settings → Secrets and variables → Actions → Secrets):
- `GPG_KEY_ID`: Launchpad 上已确认的 GPG key long ID / fingerprint
- `GPG_PRIVATE_KEY`: `gpg --armor --export-secret-keys` 导出的 ASCII armored 私钥
- `GPG_PASSPHRASE`: 私钥 passphrase

**Repository variables** (同页面 Variables tab):
- `OWNER`: Launchpad 用户名 (同时用作 `DEBFULLNAME`)
- `PPA`: PPA 名
- `DEBEMAIL`: changelog email

## Launchpad 前置

1. `gpg --full-generate-key` 生成 RSA 4096
2. Launchpad → Your account → OpenPGP keys 加公钥指纹并邮件确认
3. 创建 PPA https://launchpad.net/~<owner>/+activate-ppa
4. PPA settings 允许 `Noble Numbat (24.04)` 和 `Resolute Raccoon (26.04)` build series
5. 已签 Ubuntu Code of Conduct

## 触发方式

- cron `0 6 * * *` (UTC)
- workflow_dispatch 手动

`concurrency: ppa-upload` 保 workflow 不并发 (dput 上传竞态). 4 stage 全流程约 2-3 小时, 主要耗时在 Launchpad publish 等待.

## 文件结构

```
.github/workflows/build-ppa.yml       # series matrix 调用入口
.github/workflows/build-series.yml    # 单个 series 的 4 stage needs 链
.github/workflows/build-stage.yml     # 包并行构建 + stage upload 锁
.github/actions/setup-ppa-env/        # composite: apt/gpg/pbuilder chroot
scripts/build-and-upload.sh           # 单包: 拉源→dch→debuild→pbuilder(带A-hook)→dput
scripts/pin-build-deps.py              # 签名前提高内部 Build-Depends，生成 A-hook 共用下限
scripts/get-ppa-version.sh            # Launchpad API 查 PPA 已发布最新版本
scripts/deps.map                      # PPA 内依赖映射, 校验用
patches/{common,<series>}/<pkg>/*.patch                 # 上游源码 quilt patch (CMakeLists.txt 等, orig.tar.xz 里的文件)
debian-patches/{common,<series>}/<pkg>/*.patch          # debian/ 目录 patch (debian/control 等, debian.tar.xz 里的文件)
```

## 维护动作 checklist

**Debian upstream 新增 binary package**:
- 若该源码包已有公共 `debhelper-compat.patch` 且降了 compat 14→13，**必须**在公共 patch 里给新包补 `${shlibs:Depends}, ${misc:Depends}`（见上方 debhelper compat 14 陷阱说明）
- 检查方式：对比新旧 `debian/control`，凡 `Architecture: any` 且无 `${shlibs:Depends}` 的 stanza 都要补

**Debian upstream 改包名 / Build-Depends** (最常见维护点):
1. 更新 `scripts/deps.map` 里 dev 包名或依赖关系
2. 若跨 stage 依赖新增, 调整 workflow 里 stage 分组
3. 复核方式: 抓 9 个包的 `debian/control`, `grep '^Build-Depends'` 找 `-dev` / `libfcitx5*` / `libime*` / `libyoga*` / `librime*` / `fcitx5-modules*` / `fcitx5-module-*`

**Ubuntu 新 series**:
- 更新入口 `build-ppa.yml` 的 `matrix.series` 和 runner 映射；复用单个 series 子流程，保持 stage 的 needs 不跨发行版。各 series 使用独立 pbuilder chroot、cache key 和 artifact 下载 pattern。
- 核对目标发行版依赖版本，将仍需的改动放入公共或对应 series patch 目录，并更新离线样本/回归测试及 README 的证据记录。
- Launchpad PPA settings 允许对应 series build。

**PPA key 变更**: 无需改代码, `Fetch PPA signing key` 步骤运行时读 API.

**添加/删除追踪包**:
1. 修 workflow matrix 里对应 stage 的 `package:` 数组
2. 若新增有 PPA 内依赖, 加 deps.map 一行

**依赖校验误报**:
- 若 hook 报某 `-dev` "not installed by build-deps" — 该 dev 未真被 Build-Depends 使用, 从 deps.map 该行删除对应条目
- 若 hook 报版本落后但 upstream 已同步 — 检查 Launchpad build 是否真等到 `Published`, 或 pbuilder chroot cache 过老 (清 GH Actions cache)

**Launchpad build 失败**: workflow 会打印 build web_link, 直接看 Launchpad build log. 常见 arm64/i386 arch 编不过, 需 patch `debian/control` `Architecture:` 或加 `[!arch]` 限定, 用 `debian/patches/` 序列化.

## 排错

**dput 失败 "Unauthorized"**: GPG key 未加到 Launchpad / key 未确认 / `GPG_KEY_ID` 与 Launchpad 上的 key 不匹配.

**pbuilder A-hook fail**: hook 输出 "installed=X expected>=Y"。核对本次 run 的 local repo 是否含所需版本、pbuilder 是否正确挂载，以及上游是否已有更严格要求。Launchpad 的依赖就绪需另查目标架构二进制发布状态。

**首轮 PPA 空 bootstrap**: `Fetch PPA signing key` 步骤会写 `PPA_ALLOW_UNTRUSTED=yes`, 允许 chroot 拉未签名 PPA. 至少一个包上传+publish 后此路径消失.

**`~ppaN` 无限递增**: 说明每次触发都重新 upload 但 upstream Debian 版本没变. 检查 `get-ppa-version.sh` 返回值和 `dpkg --compare-versions` 逻辑. 期望: `NEW_N=1` 首次上传, 同一 Debian 版本再触发 skip.

**需要强制重新打包**（patch 改了但 upstream 版本未变，skip 逻辑会拦截）:
1. 去 Launchpad PPA 页面 → 找到对应包 → 点 "Delete" 删除当前版本（含所有 binary）
2. git push（若 `debian-patches/**` 已改则 push 自动触发；否则 workflow_dispatch）
3. workflow 检测到 PPA 无该版本 → 以 `~ppa2` 重新上传（`get-max-ppa-n.sh` 查全历史含 Deleted，`ppa1` 占位仍在，N 递增）
- 注意：Launchpad 删包后下游构建可能短暂失败，尽快 push 补上
- 删的是 **source publication**，会连带删 binary；只删 binary 不够，skip 逻辑查的是 source

## 决策记录 (为何这样)

- **只上传源码包让 Launchpad 编译二进制**: PPA 通常拒二进制 upload, 且 Launchpad 有 arm64/riscv64 build farm 支持多架构 — 本地 GH runner 只有 amd64.
- **`~noble1~ppa1` 后缀而非 `ubuntu1~noble1~ppa1`**: 前者更简洁, 保 Debian 原始 revision (`-1`) 语义清晰. `~` 在 dpkg 版本比较里小于任何字符, 保证 upstream 未来发新版时能正常覆盖.
- **不做主动 no-change rebuild** (依赖 bump 触发): 已有活动版本保持幂等；新准备的源码按本次 run 依赖版本提高下限，A-hook 校验本地实际安装版本。
- **独立 stage upload job**: dput 上传同一 PPA 时必须串行；构建可并行。stage 内按 package 列表顺序上传，发行版间由同一 PPA 锁协调。
- **pbuilder 而非 sbuild**: pbuilder 在 GH Actions 上启动更快, sbuild 需 schroot 配置更繁琐. 精度差别对本场景不显著.
- **chroot cache key 含 run_id + restore prefix**: 每次 workflow 建独立 cache 但可复用旧, 保证 base.tgz 每天至少刷一次 apt cache.

## AI 协作规范

**README 范围**：保持对外项目说明；内部实现细节、排错过程、具体 run/build 记录和验证命令放在维护指南或对应测试说明中。

**修改后测试**：修改 `scripts/`、打包 patch、workflow 或测试代码后，交付或 commit 前必须在仓库根目录运行 `bash scripts/test.sh`，全部通过后才能宣告完成。行为变更需同步补充或更新回归测试，覆盖版本幂等、失败重试、缓存完整性和上传判定中受影响的场景；测试失败必须修复，不能通过删除或弱化断言绕过。依赖缺失时先安装测试所需的 `python3-yaml`、`apt-utils`、`jq` 和 `dpkg-dev`；无法运行时明确报告阻塞，不能声称通过。测试在开发修改后本地执行。

**Commit 不 push**: 没有显式说 "push" / "推上去" / "上传" 时, commit 完不 push, 不询问. 用户想 push 前手动 review.

**Patch 生成流程** (禁止手写):
1. `curl` 下载原始文件到临时目录
2. 复制副本, 用工具修改副本
3. `diff -u orig new | sed 's|原路径|a/debian/...|; s|新路径|b/debian/...|'` 生成 patch
4. `patch -p1 --dry-run` 验证通过再写入

手写 patch 行号易错, context 不准导致 apply 失败.

**Stage 分组不变式**: workflow 4 stage 只按 **PPA 内 9 个包的互依赖** 分, 不看 sid 所有 Build-Depends. 判断某包能否上移: 只看其 Build-Depends 是否含追踪的 9 个包产出的 `-dev` (见 `scripts/deps.map`). 复核: `curl https://sources.debian.org/data/main/<x>/<pkg>/<ver>/debian/control` grep Build-Depends.

**plasma-wayland-protocols 放宽背景**: noble 只有 1.10.0, sid fcitx5 要求 ≥ 1.20. fcitx5 实际只用 `plasma-window-management.xml` (见 `src/lib/fcitx-wayland/plasma-window-management/CMakeLists.txt`), 该文件 1.10→1.20 是 additive (interface version 16→20), 无 API break. 若版本约束再次升高: 先确认 fcitx5 是否新用了 1.20+ 独有协议文件 (如 `kde-screen-edge-v1.xml`), 若未使用则继续放宽.
