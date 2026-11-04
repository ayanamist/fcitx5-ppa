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

入口 [build-ppa.yml](.github/workflows/build-ppa.yml) 按发行版调用 [build-series.yml](.github/workflows/build-series.yml)，每次调用只负责一个发行版的完整四阶段依赖链。某个发行版失败只阻止它自己的下游阶段，另一个发行版仍会继续；整个 run 最终仍会报告失败。

入口的 series matrix 和子流程每阶段的 package matrix 均使用 `max-parallel: 1`，保留同一 PPA 的上传顺序；入口设置 `fail-fast: false`，失败不会取消排队中的另一个发行版。全局 `concurrency: ppa-upload` 防止不同 run 并发上传。Noble 使用 `ubuntu-24.04` runner，Resolute 使用 `ubuntu-26.04` runner，分别建立对应 pbuilder chroot。缓存 key、跨阶段 artifact 名和下载 pattern 均包含 series，防止混用二进制。

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

- `common`：Noble 和 Resolute 共享的 debhelper compat 13 适配，并显式补齐运行时依赖字段。
- `noble`：Wayland/Plasma 兼容、GCC 14 选择，以及部分依赖版本约束放宽。
- Resolute 使用原始依赖约束和默认编译器，目前不需要专用 patch。

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
.github/workflows/build-ppa.yml       # 入口：按 series 调用独立子流程
.github/workflows/build-series.yml    # 单个 series 的 4 stages
.github/actions/setup-ppa-env/        # composite action: apt/gpg/pbuilder
scripts/build-and-upload.sh           # 单包: 拉源→pbuilder→dput
scripts/get-ppa-version.sh            # 查 PPA 已发布最新版本
scripts/check-ppa-series.sh           # 判断目标发行版 PPA 索引是否已发布
scripts/configure-apt-sources.sh      # 为 runner 配置独立的构建软件源
debian-patches/common/<pkg>/*.patch   # 两套发行版共享的打包兼容 patch
debian-patches/noble/<pkg>/*.patch    # Noble 专用的依赖/编译器调整
patches/noble/<pkg>/*.patch           # Noble 专用上游 quilt patch
tests/fixtures/debian-sid/            # 固定版本源码，离线验证 patch
```

## 本地检查

```bash
bash scripts/test.sh
actionlint .github/workflows/build-ppa.yml .github/workflows/build-series.yml
```
