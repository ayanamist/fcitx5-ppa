# fcitx5-ppa

自动化: 每天检查 Debian sid 中 fcitx5 相关包新版本, 分别重打成 Ubuntu 24.04 (noble) 和 Ubuntu 26.04 (resolute) 源码包并上传到 Launchpad PPA `ppa:ayanamist/fcitx5`.

## 追踪的包

- fcitx5
- fcitx5-chinese-addons
- fcitx5-gtk
- fcitx5-lua
- fcitx5-qt
- fcitx5-rime
- libime
- librime
- yoga

## 版本策略

Debian sid 版本 `X` → PPA 版本 `X~noble1~ppa1` / `X~resolute1~ppa1`.
若 PPA 已存在同前缀, 递增 `~ppaN`.
若目标发行版的 PPA 版本 ≥ 期望版本, 跳过上传；本地 deb 缓存不完整时仍重建以供下游使用.

## 构建策略

四阶段串行, 每阶段的 package × series matrix 使用 `max-parallel: 1` 保证 dput 顺序. Noble 使用 `ubuntu-24.04` runner，Resolute 使用 `ubuntu-26.04` runner，分别建立对应 pbuilder chroot。缓存 key、跨阶段 artifact 名和下载 pattern 均包含 series，防止混用二进制。某阶段任一发行版失败时，整个下一阶段不会启动。

- **Stage 1** 无 PPA 内依赖: `librime`, `yoga`, `fcitx5-gtk`
- **Stage 2** 需 `yoga`: `fcitx5`
- **Stage 3** 需 `fcitx5` (fcitx5-rime 也需 `librime`): `libime`, `fcitx5-lua`, `fcitx5-qt`, `fcitx5-rime`
- **Stage 4** 需 `fcitx5` + `libime` + `fcitx5-lua` + `fcitx5-qt`: `fcitx5-chinese-addons`

单包流程:
1. `apt-get source` 拉 Debian sid 源码
2. 按目标发行版应用 patch，计算 `~<series>1~ppaN` 版本, `dch` 加 changelog
3. `debuild -S -sa` 生成签名源码包
4. **`pbuilder` 本地测试编译** (chroot 含本 PPA, 缓存到 GH Actions cache)
   - **A-hook 校验**: build-dep 装完后 `dpkg-query` 校验关键 `-dev` 包版本 ≥ 同一发行版 PPA 上游最新版本, 不足则 fail
5. 再次检查 Launchpad 历史，确认无需跳过后用 `dput ppa:ayanamist/fcitx5` 上传源码包。
6. 后续阶段通过本次 run 的 artifact 建本地 apt repo，取得上游刚编出的 `-dev` 包。

依赖表在 [scripts/deps.map](scripts/deps.map). Debian upstream 若改 Build-Depends, 需人工更新.

## 发行版与 patch

先应用 `debian-patches/common/<pkg>/*.patch`，再应用 `debian-patches/<series>/<pkg>/*.patch`；上游 quilt patch 同样按 `patches/common/<pkg>/`、`patches/<series>/<pkg>/` 的顺序注入。各目录内按文件名排序。

2026-11-04 核对 Debian sid `fcitx5 5.1.23-1`、`fcitx5-chinese-addons 5.1.15-1`、`fcitx5-lua 5.0.18-1`、`librime 1.17.0+dfsg2-2` 和 Ubuntu Resolute 官方 amd64 包索引后的结论：

| 兼容改动 | Noble 24.04 | Resolute 26.04 | 依据 |
|---|---|---|---|
| compat 14→13，同时显式补 `${shlibs:Depends}, ${misc:Depends}` | 保留 | 保留 | Resolute 的 debhelper 13.31ubuntu1 的 `Provides` 最高仍为 `debhelper-compat (= 13)` |
| WaylandProtocols 1.46→1.45 | 保留 | 无需应用 | Resolute 的 wayland-protocols 为 1.47-1 |
| 使用内置 Plasma XML，删除系统 plasma-wayland-protocols 约束 | 保留 | 无需应用 | Resolute 的 plasma-wayland-protocols 为 1.20.0-2，满足 ≥1.20 |
| fcitx5 强制 GCC 14 | 保留 | 无需应用 | Resolute 默认 GCC 15.2，使用默认工具链 |
| 放宽 librime 的 marisa/opencc/utfcpp/yaml-cpp 约束 | 保留 | 无需应用 | Resolute 分别为 0.3.1+git20250817-1build1、1.1.9+ds1-4build1、4.0.9-1、0.8.0+dfsg-9，满足原约束 |
| 放宽 chinese-addons 的 OpenCC 约束 | 保留 | 无需应用 | 同上，满足 ≥1.1.9~ |

证据和复核入口：

- Ubuntu 官方 [Resolute main 包索引](https://archive.ubuntu.com/ubuntu/dists/resolute/main/binary-amd64/Packages.xz)、[universe 包索引](https://archive.ubuntu.com/ubuntu/dists/resolute/universe/binary-amd64/Packages.xz)。可用 `curl -fsSL <链接> | xz -dc` 查看 `Package`、`Version`、`Provides`；`dpkg --compare-versions <实际版本> ge <要求版本>` 复核约束。
- [Resolute debhelper](https://packages.ubuntu.com/resolute/debhelper) 的 [deb 文件](https://archive.ubuntu.com/ubuntu/pool/main/d/debhelper/debhelper_13.31ubuntu1_all.deb)：下载后执行 `dpkg-deb -f <deb文件> Provides` 可复核 compat。13.31 的代码包含实验 compat 14 支持，但未提供源码包要求的虚包，仍需降至 13。
- [GitHub Ubuntu 26.04 runner 正式支持公告](https://github.blog/changelog/2026-09-17-ubuntu-26-generally-available-and-latest-migration/)。使用同版本 runner，避免 Noble 的 debootstrap 1.0.134ubuntu2 缺少 `resolute` suite 脚本。
- [固定版本的 Debian 源码测试样本与下载来源](tests/fixtures/debian-sid/README.md)。运行 `bash scripts/test.sh` 可离线验证补丁选择、实际 patch 应用、全部 arch-any 包的依赖字段，以及两套发行版的版本幂等、重试、上传判定、缓存和 artifact 隔离。

上述版本核对范围是 Resolute release 的 amd64 索引；后续 Debian/Ubuntu 更新仍需复核。本地测试不代表已完成 pbuilder 全量编译或 Launchpad 多架构构建，首次发布需由实际 workflow 验证。

## 触发

- cron: `0 6 * * *` (UTC)
- workflow_dispatch (手动)

## 需要的 GitHub Secrets

| Secret | 说明 |
|---|---|
| `GPG_KEY_ID` | 已上传到 Launchpad 的 GPG key long ID (16 hex 或 fingerprint) |
| `GPG_PRIVATE_KEY` | ASCII armored 私钥, `gpg --armor --export-secret-keys KEYID` 导出 |
| `GPG_PASSPHRASE` | 私钥 passphrase |

## 需要的 GitHub Repository Variables

Settings → Secrets and variables → Actions → **Variables** tab:

| Variable | 示例 | 说明 |
|---|---|---|
| `OWNER` | `ayanamist` | Launchpad 用户名, 同时用作 `DEBFULLNAME` |
| `PPA` | `fcitx5` | PPA 名 |
| `DEBEMAIL` | `ayanamist@gmail.com` | changelog email |

准备步骤:
1. `gpg --full-generate-key` 生成 RSA 4096.
2. Launchpad → Your account → OpenPGP keys 上传公钥指纹.
3. 到 https://launchpad.net/~ayanamist/+editpgpkeys 通过邮件确认.
4. 私钥导出后加进 GitHub Secrets.

## Launchpad PPA 前置

- 账户: https://launchpad.net/~ayanamist
- PPA: https://launchpad.net/~ayanamist/+archive/ubuntu/fcitx5
- Launchpad Code of Conduct 已签署
- PPA settings 里允许 Noble Numbat (24.04) 和 Resolute Raccoon (26.04) 构建

## 手动触发

Actions → "Build and upload to PPA" → Run workflow.

## 结构

```
.github/workflows/build-ppa.yml       # 4 stages × noble/resolute
.github/actions/setup-ppa-env/        # composite action: apt/gpg/pbuilder
scripts/build-and-upload.sh           # 单包: 拉源→pbuilder→dput
scripts/get-ppa-version.sh            # 查 PPA 已发布最新版本
debian-patches/common/<pkg>/*.patch   # 两套发行版共享的打包兼容 patch
debian-patches/noble/<pkg>/*.patch    # Noble 专用的依赖/编译器调整
patches/noble/<pkg>/*.patch           # Noble 专用上游 quilt patch
tests/fixtures/debian-sid/            # 固定版本源码，离线验证 patch
```
