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

入口 [build-ppa.yml](.github/workflows/build-ppa.yml) 同时启动 Noble 和 Resolute，两套发行版各有独立的四阶段依赖链。每个阶段由 [build-stage.yml](.github/workflows/build-stage.yml) 并行构建该阶段的所有包；某个包失败不取消同阶段其他构建，但会阻止该发行版进入下一阶段。

编译完成后，阶段的独立 upload job 按 package 列表顺序上传签名源码包。只有 upload job 使用同一 PPA 的 `ppa-transfer-<owner>-<ppa>` concurrency 锁，构建任务不上锁。阶段间通过本次 run 的 deb artifact 传递依赖，所有缓存和 artifact 均包含 series。

全局 `concurrency: ppa-upload` 保持不同 workflow run 串行，避免版本分配竞态。单个 run 内两套发行版并行，且每套只有一个活动阶段，因此同时最多有两个 upload job 争用 PPA 锁，可安全使用 GitHub 的一个运行、一个等待队列。增加发行版或改变阶段依赖关系时，需同步调整上传队列容量。

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
5. 准备包含 `.changes`、`.dsc`、全部 orig/component tarball、校验信息和签名公钥的源码 artifact；私钥只用于构建任务的签名。
6. upload job 取得 PPA 锁后验证 artifact，并重新检查 Launchpad 历史，确认无需跳过后用 `dput ppa:ayanamist/fcitx5` 上传。
7. 后续阶段通过本次 run 的 deb artifact 建本地 apt repo，取得上游刚编出的 `-dev` 包。

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
.github/workflows/build-stage.yml     # stage 内并行构建、独立串行上传
.github/actions/setup-ppa-env/        # composite action: apt/gpg/pbuilder
scripts/build-and-upload.sh           # 单包: 拉源→pbuilder→准备签名源码
scripts/source-artifact.py           # 完整源码 artifact 的准备与校验
scripts/upload-source.sh             # 持锁后复查版本并 dput
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
actionlint .github/workflows/build-ppa.yml .github/workflows/build-series.yml .github/workflows/build-stage.yml
```
