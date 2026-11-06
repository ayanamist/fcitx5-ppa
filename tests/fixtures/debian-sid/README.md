# Debian sid patch fixtures

2026-11-04 从 Debian 官方 archive 下载，用于 `tests/test_series_patches.py` 离线执行实际补丁路径。文件保持原样，测试在临时副本上应用公共 patch 和发行版 patch；测试在 changelog/signing 前终止，不执行 pbuilder 或 dput。

| 目录 | 源码版本 | 文件 | 来源 |
|---|---|---|---|
| fcitx5 | 5.1.23-1 | debian/control、debian/rules | [debian.tar.xz](https://deb.debian.org/debian/pool/main/f/fcitx5/fcitx5_5.1.23-1.debian.tar.xz) |
| fcitx5 | 5.1.23 | CMakeLists.txt | [orig.tar.xz](https://deb.debian.org/debian/pool/main/f/fcitx5/fcitx5_5.1.23.orig.tar.xz) |
| fcitx5-chinese-addons | 5.1.15-1 | debian/control | [debian.tar.xz](https://deb.debian.org/debian/pool/main/f/fcitx5-chinese-addons/fcitx5-chinese-addons_5.1.15-1.debian.tar.xz) |
| fcitx5-lua | 5.0.18-1 | debian/control | [debian.tar.xz](https://deb.debian.org/debian/pool/main/f/fcitx5-lua/fcitx5-lua_5.0.18-1.debian.tar.xz) |
| librime | 1.17.0+dfsg2-2 | debian/control | [debian.tar.xz](https://deb.debian.org/debian/pool/main/libr/librime/librime_1.17.0+dfsg2-2.debian.tar.xz) |

更新样本时先下载对应原始 tarball，再用 `tar -xOf <debian.tar.xz> debian/control`（orig 中路径带 upstream 目录前缀）提取文件；同时更新本表和测试中受版本影响的断言。

验证：在仓库根目录运行 `bash scripts/test.sh`。测试证明样本上的补丁可应用和发行版选择正确，后续 upstream 版本的适配与实际构建结果仍由 workflow 检验。

## 九包构建依赖元数据

`ppa-build-deps.json` 于 2026-11-06 从 [Debian sid Sources.xz](https://deb.debian.org/debian/dists/sid/main/source/Sources.xz) 提取九个追踪源码包的 `Version`、`Binary`、`Build-Depends{,-Arch,-Indep}` 字段。版本分别为 fcitx5 5.1.23-1、chinese-addons 5.1.15-1、gtk 5.1.7-2、lua 5.0.18-1、qt/rime 5.1.16-1、libime 1.1.17-1、librime 1.17.0+dfsg2-3、yoga 3.2.1+ds-3；librime 的完整 control patch 样本仍保留上表的 -2 版本。

`test_pin_build_deps.py` 用该元数据核对 `deps.map` 是否覆盖所有 PPA 内构建依赖，并验证两套发行版的最低版本提升。测试构造的本地 Packages 使用样本版本加各自 PPA 后缀，不代表已经发布的二进制。源码包回归会执行真实 `dpkg-source -b`，再用 `dpkg-checkbuilddeps` 验证旧 libime 被拒绝；不执行完整编译或远端上传。

更新时下载 Sources.xz，用 Python `lzma` 读取 deb822 段落，按 `Package` 提取上述字段；同包存在多个版本时按 `dpkg --compare-versions` 选最新版。同步复核依赖映射后，在仓库根目录执行 `bash scripts/test.sh`。
