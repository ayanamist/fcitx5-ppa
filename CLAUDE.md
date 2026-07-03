# fcitx5-ppa 维护指南

自动化 GitHub Action, 每天从 Debian sid 拉取 fcitx5 相关源码包, 重打 `~noble1~ppaN` 后缀, 上传 Launchpad PPA `ppa:ayanamist/fcitx5` 供 Ubuntu 24.04 使用.

## 追踪的包 (8 个)

`fcitx5`, `fcitx5-chinese-addons`, `fcitx5-gtk`, `fcitx5-qt`, `fcitx5-rime`, `libime`, `librime`, `yoga`

## 依赖图 (源自 Debian sid `debian/control` Build-Depends)

```
Stage 1 (leaves, 无 PPA 内依赖):  librime, yoga, fcitx5-gtk
Stage 2 (需 yoga):                fcitx5
Stage 3 (需 fcitx5[+librime]):    libime, fcitx5-qt, fcitx5-rime
Stage 4 (需 fcitx5+libime+qt):    fcitx5-chinese-addons
```

依赖真相由 `scripts/deps.map` 记录, 用 pbuilder A-hook 校验编译时实际 `-dev` 版本. Debian upstream 若改 Build-Depends 必须人工更新此表. 复核方式: `https://sources.debian.org/src/<pkg>/sid/debian/control/`.

**已知细节**:
- `fcitx5-gtk` 只依赖 gtk/glib/x11, 不依赖 fcitx5 本体, 属 leaf
- `fcitx5-chinese-addons` 分裂依赖 `libimecore/pinyin/table-dev` 三个二进制包, 都来自 `libime` 源码
- `libfcitx5-qt6-dev` 命名可能随 Qt 版本变化 (Qt5 时代是 `libfcitx5-qt5-dev`), Debian 换 Qt 时需改 deps.map
- `fcitx5-modules-dev` 与 `fcitx5-module-lua-dev` 都来自 `fcitx5` 源码

## 版本策略

Debian sid 版本 `X` → PPA 版本 `X~noble1~ppa1`. 相同前缀已存在则 `~ppaN+1` 递增. PPA 版本 ≥ 期望首个 `~ppa1` 则 skip (说明已发过或 upstream 未变). 不需为 "依赖版本变了" 主动 bump — 相信 Debian maintainer 会 bump upstream, 自动流程只做 rebuild.

## 构建流程 (per package)

1. `apt-get source <pkg>` 拉 sid 源码 (workflow 在 apt sources 加 `deb-src http://deb.debian.org/debian sid main` + apt pin `-1` 阻止拉 Debian 二进制)
2. `dpkg-parsechangelog -SVersion` 拿 Debian 版本
3. Launchpad REST API 查 PPA 现有版本, 计算 `~ppaN`
4. `dch` 生成 changelog 条目, `debuild -S -sa -k$GPG_KEY_ID` 生成签名源码包
5. **pbuilder 本地测试编译** (含 A-hook 依赖版本校验), 缓存 chroot 到 `actions/cache@v4` (key 含 run_id 强制每天刷新, restore-key 前缀匹配复用)
6. `dput ppa:$OWNER/$PPA` 上传
7. 轮询 Launchpad API (`getPublishedSources` → `getBuilds` → `getPublishedBinaries`), 状态全 `Published` 才算 stage 完成. 超时 7200s (120 min).

Stage 通过 `needs:` 串行, `fail-fast: false` + `max-parallel: 1` 保 dput 顺序. Job 之一失败 → 后续 stage 不启动 (符合预期, 保护下游).

## 依赖版本一致性校验 (关键)

**问题**: pbuilder 装 build-dep 时可能因 apt cache/mirror 时序拿到旧版 `-dev` header, 编出的二进制 ABI 与 PPA 上传的新库不匹配.

**方案**: pbuilder A-hook (装完 build-dep 后, build 前运行) 里 `dpkg-query` 查每个关键 `-dev` 实际版本, 与 PPA 上游最新版本比较 (`dpkg --compare-versions`), 不足即 fail step, 不上传.

**注意**: **必须 A-hook 不能 D-hook**. D-hook 在 build-dep 装**之前**触发, dpkg-query 全空 → 全部 skip, 校验形同虚设.

**跳过场景**:
- 上游包在 PPA 无版本 (首次 bootstrap) → warning 跳过
- Build-Depends 未列该 dev 包 (dpkg-query 空) → warning 跳过
- 均不阻塞

## Noble 适配 patch

**两类 patch**:

**`patches/<pkg>/`** — quilt 格式，改**上游源码文件**（orig.tar.xz 里的文件，如 CMakeLists.txt）。build-and-upload.sh 复制到 `debian/patches/` 并追加 `series`，dpkg-source `--before-build` 钩子 apply。

**`debian-patches/<pkg>/`** — 标准 unified diff，改 **`debian/` 目录下的文件**（debian.tar.xz 里的文件，如 debian/control）。build-and-upload.sh 在 debuild 前直接 `patch -p1` apply。**不能加入 quilt series**：dpkg-source -b 验证时从 orig 重跑，orig 里无 debian/，会报 "Reversed or previously applied"。

**判断"API 兼容"**: 看 upstream changelog / SONAME 变化. 若 SONAME 未变，通常 header ABI 兼容. 编译失败说明真断 API，得升级 noble 的 lib 或换其他策略.

**当前 patch**:
- `patches/fcitx5/relax-wayland-protocols-version.patch`: CMakeLists.txt `WaylandProtocols 1.46→1.45`，`USE_SYSTEM_PLASMA_WAYLAND_PROTOCOLS On→Off`（用自带 XML，noble 的 1.10 缺 org_kde_plasma_stacking_order）
- `debian-patches/fcitx5/noble-compat.patch`: debhelper-compat 14→13，删 plasma-wayland-protocols dep，**全部 arch-any binary package 补 `${shlibs:Depends}, ${misc:Depends}`**
- `debian-patches/librime/noble-compat.patch`: 删 libmarisa-dev / libopencc-dev / libutfcpp-dev / libyaml-cpp-dev 版本约束
- `debian-patches/fcitx5-chinese-addons/noble-compat.patch`: debhelper-compat 14→13，删 libopencc-dev 版本约束，**全部 arch-any binary package 补 `${shlibs:Depends}, ${misc:Depends}`**
- `debian-patches/fcitx5-lua/noble-compat.patch`: debhelper-compat 14→13，**全部 arch-any binary package 补 `${shlibs:Depends}, ${misc:Depends}`**

**⚠️ debhelper compat 14 陷阱**：debhelper compat 14 新增特性：`dh_gencontrol` 自动将 `${shlibs:Depends}` 和 `${misc:Depends}` 注入所有 binary package 的依赖字段，即使 `debian/control` 里没有显式写出。Debian upstream（fcitx5/fcitx5-chinese-addons/fcitx5-lua）已依赖此特性，`debian/control` 里的库包 stanza 完全省略了 `Depends` 字段。Noble 只有 debhelper 13.x，**必须降到 compat 13**，但降级后自动注入失效，导致 `libfcitx5core7` 等包的 `Depends` 为空（无 `libxkbcommon0`、无 `libc6`），下游包 pbuilder 构建时找不到 `.so` 而 link 失败。**修法**：凡是降 compat 14→13 的包，必须在 `noble-compat.patch` 里给全部 arch-any binary package 手动补上 `${shlibs:Depends}, ${misc:Depends}`。**Debian upstream 新增包时也要同步更新 patch。**

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
4. PPA settings 勾 `Noble Numbat (24.04)` build series
5. 已签 Ubuntu Code of Conduct

## 触发方式

- cron `0 6 * * *` (UTC)
- workflow_dispatch 手动

`concurrency: ppa-upload` 保 workflow 不并发 (dput 上传竞态). 4 stage 全流程约 2-3 小时, 主要耗时在 Launchpad publish 等待.

## 文件结构

```
.github/workflows/build-ppa.yml       # 4 stage matrix workflow
.github/actions/setup-ppa-env/        # composite: apt/gpg/pbuilder chroot
scripts/build-and-upload.sh           # 单包: 拉源→dch→debuild→pbuilder(带A-hook)→dput
scripts/get-ppa-version.sh            # Launchpad API 查 PPA 已发布最新版本
scripts/wait-for-publish.sh           # 轮询 Published (含 build 状态检测/失败态早退)
scripts/deps.map                      # PPA 内依赖映射, 校验用
patches/<pkg>/*.patch                 # 上游源码 quilt patch (CMakeLists.txt 等, orig.tar.xz 里的文件)
debian-patches/<pkg>/*.patch          # debian/ 目录 patch (debian/control 等, debian.tar.xz 里的文件)
```

## 维护动作 checklist

**Debian upstream 新增 binary package**:
- 若该源码包已有 `noble-compat.patch` 且降了 compat 14→13，**必须**在 patch 里给新包补 `${shlibs:Depends}, ${misc:Depends}`（见上方 debhelper compat 14 陷阱说明）
- 检查方式：对比新旧 `debian/control`，凡 `Architecture: any` 且无 `${shlibs:Depends}` 的 stanza 都要补

**Debian upstream 改包名 / Build-Depends** (最常见维护点):
1. 更新 `scripts/deps.map` 里 dev 包名或依赖关系
2. 若跨 stage 依赖新增, 调整 workflow 里 stage 分组
3. 复核方式: 抓 8 个包的 `debian/control`, `grep '^Build-Depends'` 找 `-dev` / `libfcitx5*` / `libime*` / `libyoga*` / `librime*` / `fcitx5-modules*` / `fcitx5-module-*`

**Ubuntu 新 series** (如 25.04):
- 修 workflow `env.SERIES` 或改成 matrix 支持多 series (需 pbuilder chroot 各建一份)
- Launchpad PPA settings 里勾对应 series build

**PPA key 变更**: 无需改代码, `Fetch PPA signing key` 步骤运行时读 API.

**添加/删除追踪包**:
1. 修 workflow matrix 里对应 stage 的 `package:` 数组
2. 若新增有 PPA 内依赖, 加 deps.map 一行

**依赖校验误报**:
- 若 hook 报某 `-dev` "not installed by build-deps" — 该 dev 未真被 Build-Depends 使用, 从 deps.map 该行删除对应条目
- 若 hook 报版本落后但 upstream 已同步 — 检查 wait-for-publish.sh 是否真等到 `Published`, 或 pbuilder chroot cache 过老 (清 GH Actions cache)

**Launchpad build 失败**: `wait-for-publish.sh` 会打印 build web_link, 直接看 Launchpad build log. 常见 arm64/i386 arch 编不过, 需 patch `debian/control` `Architecture:` 或加 `[!arch]` 限定, 用 `debian/patches/` 序列化.

## 排错

**dput 失败 "Unauthorized"**: GPG key 未加到 Launchpad / key 未确认 / `GPG_KEY_ID` 与 Launchpad 上的 key 不匹配.

**pbuilder A-hook fail**: hook 输出 "installed=X expected>=Y". 说明前 stage 上传成功但 apt cache 未刷新到. 检查 stage 之间 `wait-for-publish.sh` 是否成功等到 `Published` (含二进制).

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
- **不做主动 no-change rebuild** (依赖 bump 触发): 相信 Debian maintainer 处理 SONAME/ABI. 用 D-hook 兜底检测 pbuilder 编译期版本一致性.
- **每 stage `max-parallel: 1`**: dput 无锁, 并发上传同 PPA 会竞态. Launchpad 也可能因 orig tarball 上传时序错乱拒收.
- **pbuilder 而非 sbuild**: pbuilder 在 GH Actions 上启动更快, sbuild 需 schroot 配置更繁琐. 精度差别对本场景不显著.
- **chroot cache key 含 run_id + restore prefix**: 每次 workflow 建独立 cache 但可复用旧, 保证 base.tgz 每天至少刷一次 apt cache.

## AI 协作规范

**Commit 不 push**: 没有显式说 "push" / "推上去" / "上传" 时, commit 完不 push, 不询问. 用户想 push 前手动 review.

**Patch 生成流程** (禁止手写):
1. `curl` 下载原始文件到临时目录
2. 复制副本, 用工具修改副本
3. `diff -u orig new | sed 's|原路径|a/debian/...|; s|新路径|b/debian/...|'` 生成 patch
4. `patch -p1 --dry-run` 验证通过再写入

手写 patch 行号易错, context 不准导致 apply 失败.

**Stage 分组不变式**: workflow 4 stage 只按 **PPA 内 8 个包的互依赖** 分, 不看 sid 所有 Build-Depends. 判断某包能否上移: 只看其 Build-Depends 是否含追踪的 8 个包产出的 `-dev` (见 `scripts/deps.map`). 复核: `curl https://sources.debian.org/data/main/<x>/<pkg>/<ver>/debian/control` grep Build-Depends.

**plasma-wayland-protocols 放宽背景**: noble 只有 1.10.0, sid fcitx5 要求 ≥ 1.20. fcitx5 实际只用 `plasma-window-management.xml` (见 `src/lib/fcitx-wayland/plasma-window-management/CMakeLists.txt`), 该文件 1.10→1.20 是 additive (interface version 16→20), 无 API break. 若版本约束再次升高: 先确认 fcitx5 是否新用了 1.20+ 独有协议文件 (如 `kde-screen-edge-v1.xml`), 若未使用则继续放宽.
