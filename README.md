# fcitx5-ppa

自动化: 每天检查 Debian sid 中 fcitx5 相关包新版本, 重打成 Ubuntu 24.04 (noble) 源码包并上传到 Launchpad PPA `ppa:ayanamist/fcitx5`.

## 追踪的包

- fcitx5
- fcitx5-chinese-addons
- fcitx5-gtk
- fcitx5-qt
- fcitx5-rime
- libime
- librime
- yoga

## 版本策略

Debian sid 版本 `X` → PPA 版本 `X~noble1~ppa1`.
若 PPA 已存在同前缀, 递增 `~ppaN`.
若 PPA 版本 ≥ 期望版本, 跳过.

## 构建策略

四阶段串行, 每阶段 `max-parallel: 1` 保证 dput 顺序. 阶段划分依据 sid `debian/control` 的 Build-Depends (2026-07 抓取):

- **Stage 1** 无 PPA 内依赖: `librime`, `yoga`, `fcitx5-gtk`
- **Stage 2** 需 `yoga`: `fcitx5`
- **Stage 3** 需 `fcitx5` (fcitx5-rime 也需 `librime`): `libime`, `fcitx5-qt`, `fcitx5-rime`
- **Stage 4** 需 `fcitx5` + `libime` + `fcitx5-qt`: `fcitx5-chinese-addons`

单包流程:
1. `apt-get source` 拉 Debian sid 源码
2. 计算 `~noble1~ppaN` 版本, `dch` 加 changelog
3. `debuild -S -sa` 生成签名源码包
4. **`pbuilder` 本地测试编译** (chroot 含本 PPA, 缓存到 GH Actions cache)
   - **D-hook 校验**: build-dep 装完后 `dpkg-query` 校验关键 `-dev` 包版本 ≥ PPA 上游最新版本, 不足则 fail
5. `dput ppa:ayanamist/fcitx5` 上传源码包
6. 轮询 Launchpad API 直到 `Published` 且所有二进制发布 (超时 120 min)

Stage 之间必须等前一 stage 全部 Published, 保证下一阶段 pbuilder 拉到新的 `-dev` 包.

依赖表在 [scripts/deps.map](scripts/deps.map). Debian upstream 若改 Build-Depends, 需人工更新.

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
- PPA settings 里 series 勾上 Noble Numbat (24.04)

## 手动触发

Actions → "Build and upload to PPA" → Run workflow.

## 结构

```
.github/workflows/build-ppa.yml       # 3 stages
.github/actions/setup-ppa-env/        # composite action: apt/gpg/pbuilder
scripts/build-and-upload.sh           # 单包: 拉源→pbuilder→dput
scripts/get-ppa-version.sh            # 查 PPA 已发布最新版本
```
