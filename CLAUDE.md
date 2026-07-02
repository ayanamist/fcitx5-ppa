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

## Sid → Noble build-dep 放宽 (relax-deps.map)

**问题**: sid `debian/control` 里 build-dep 常写 `libX-dev (>= NEW_VER)`, 但 noble 里该库还是老版本. pbuilder-satisfydepends 直接失败.

**方案**: 若 API 兼容 (通常小版本号变化只是打包 revision, 未真断 API), 在 dch 前 perl 改写 `debian/control`. 支持两种 spec 语法:
- `dep` — 删所有版本约束 (>= X) / (>> X) / (= X), 只保留包名
- `dep=VER` — 改成 `(= VER)`, 用于 `debhelper-compat` 这类精确匹配虚拟包 (删约束会导致 aptitude 找不到实包)

**表**: `scripts/relax-deps.map`, 每行 `<package> <spec> [<spec> ...]`.

**判断"API 兼容"**: 看 upstream changelog / SONAME 变化. 若 `libmarisa` SONAME 未变 (`libmarisa0`), 通常 header ABI 兼容. 编译失败说明真断 API, 得升级 noble 的 lib 或换其他策略.

**当前放宽**:
- `librime`: `libmarisa-dev`, `libopencc-dev`, `libutfcpp-dev`, `libyaml-cpp-dev` (删版本约束)
- `fcitx5` / `fcitx5-chinese-addons`: `debhelper-compat=13` (sid 用 14, noble 只到 13, compat 13→14 diff 小通常兼容)

新增放宽项流程:
1. 看 pbuilder log 里 `pbuilder-satisfydepends-dummy` 装不了的包
2. 确认是版本约束问题而非包不存在: `apt-cache policy <pkg>` 里 noble 版本 < 约束版本
3. 加进 `relax-deps.map` 该 package 那一行
4. 重跑 workflow, 编译过 = API 兼容; 失败 = 需要真升级依赖或找别的路径

**为何不用 patch 文件替代**: `debian/control` 的版本约束 (`>= X`) 里的 X 随 upstream 每次 bump 变化, patch context 会频繁失效. perl regex 版本无关, 更稳健. patch 适合改上游源码 (CMakeLists.txt 等不频繁动的文件).

## 上游源码 patch (patches/)

**问题**: CMakeLists.txt 等上游文件里硬写了版本号 (如 `find_package(WaylandProtocols 1.46)`), noble 可用版本稍低, 但 API 实际兼容.

**方案**: `patches/<pkg>/` 存储 quilt 格式 patch 文件. build-and-upload.sh 在 dch 前自动复制到 `debian/patches/` 并追加 `series`, dpkg-source 3.0(quilt) 的 `--before-build` 钩子会 `quilt push -a` 应用.

**与 relax-deps.map 的区别**:
- `relax-deps.map` → 改 `debian/control` Build-Depends 版本约束 (regex, 版本无关, 适合高频变动的约束字符串)
- `patches/` → 改上游源码文件 (quilt patch, 精确 context, 适合低频变动的硬编码版本号)

**当前 patch**:
- `patches/fcitx5/relax-wayland-protocols-version.patch`: CMakeLists.txt `WaylandProtocols 1.46 → 1.45` + `PlasmaWaylandProtocols 1.20 → 1.10` (noble 分别有 1.45/1.10, 版本 bump 均为 additive-only)

新增 patch 流程:
1. 看 pbuilder log 里 cmake/configure 报 `Could NOT find X: Required is at least version "Y"`
2. 确认 noble 已有版本 < Y 但 API 兼容 (changelog/SONAME 无破坏性变更)
3. 在 `patches/<pkg>/` 新建 `<描述>.patch` (quilt 格式, `--- a/` `+++ b/` 相对包根)
4. 重跑 workflow 验证

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
scripts/relax-deps.map                # debian/control Build-Depends 版本放宽 (regex, 版本无关)
patches/<pkg>/*.patch                 # 上游源码 quilt patch (CMakeLists.txt 等低频文件)
```

## 维护动作 checklist

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

## 决策记录 (为何这样)

- **只上传源码包让 Launchpad 编译二进制**: PPA 通常拒二进制 upload, 且 Launchpad 有 arm64/riscv64 build farm 支持多架构 — 本地 GH runner 只有 amd64.
- **`~noble1~ppa1` 后缀而非 `ubuntu1~noble1~ppa1`**: 前者更简洁, 保 Debian 原始 revision (`-1`) 语义清晰. `~` 在 dpkg 版本比较里小于任何字符, 保证 upstream 未来发新版时能正常覆盖.
- **不做主动 no-change rebuild** (依赖 bump 触发): 相信 Debian maintainer 处理 SONAME/ABI. 用 D-hook 兜底检测 pbuilder 编译期版本一致性.
- **每 stage `max-parallel: 1`**: dput 无锁, 并发上传同 PPA 会竞态. Launchpad 也可能因 orig tarball 上传时序错乱拒收.
- **pbuilder 而非 sbuild**: pbuilder 在 GH Actions 上启动更快, sbuild 需 schroot 配置更繁琐. 精度差别对本场景不显著.
- **chroot cache key 含 run_id + restore prefix**: 每次 workflow 建独立 cache 但可复用旧, 保证 base.tgz 每天至少刷一次 apt cache.
