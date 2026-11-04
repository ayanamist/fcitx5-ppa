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
