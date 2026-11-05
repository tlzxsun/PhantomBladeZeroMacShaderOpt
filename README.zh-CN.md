# PhantomBladeZeroMacShaderOpt

[English](README.md) | 简体中文

针对 macOS CrossOver 中《影之刃零》**Phantom Blade Zero（PBZ.exe）**反复预编译 shader 的实验性方案。仅分发源码和脚本，在已测试的系统上将传给 Metal 文件系统编译缓存的预算从 **1 GiB 扩大到 2 GiB**。

## 已验证的结果

| 项目 | 实测情况 |
| --- | --- |
| 硬件 | Apple M5，24 GB 内存 |
| macOS | 27.2，构建号 `26B123` |
| CrossOver | 26.3，构建号 `26.3.0.39832`，x86_64 Wine 11.0 |
| 游戏与图形层 | Phantom Blade Zero，D3DMetal |
| 修改前 | 52,883 条有效函数缓存记录，1,073,741,688 字节，接近 1 GiB |
| 修改后 | 119,740 条有效记录，1,811,547,872 字节，约 **1.69 GiB** |
| 重新启动游戏 | 新游戏进程以 2 GiB 预算打开扩容后的缓存，主观感受启动明显加快 |

这些是单台机器的观察结果，不是受控性能测试。尚未验证填满整个 2 GiB 空间的表现。包括预编译接近 99% 的等待在内，部分停顿仍然存在。本项目不承诺提高 FPS 或消除全部卡顿。

路径过滤也识别 Mullion 的 `PBZ.exe` 缓存，但**本次成功的完整游戏测试使用 D3DMetal**，不能据此声称 Mullion 已得到同等验证。

## 安装前须知

本方案会修改 **CrossOver 共用的两个 Wine 启动器的签名**。注入配置只写入指定瓶子，但启动器文件是同一套 CrossOver 中各瓶子共用的。安装时会保存原文件，以便恢复。

- 安装和恢复前，退出**所有瓶子**中的 Wine、Steam 和游戏。
- 使用自己安装的 CrossOver。本仓库不分发 CrossOver、Wine、Apple 或预编译 hook 二进制文件。
- 安装 Xcode Command Line Tools：`xcode-select --install`。需要 Python 3.9+、Clang 和 `codesign`。
- CrossOver 所在目录需要可写。**不要使用 `sudo` 运行安装脚本**。
- 若使用过手动修改或旧实验补丁，先用对应备份恢复。脚本会拒绝已经修改过的启动器，避免把修改版误存为“原版备份”。
- 自行备份重要数据。脚本备份启动器和配置，不会完整备份瓶子或 shader 缓存。

默认只接受上表中准确的 macOS、CrossOver 构建号。`--allow-untested` 仅绕过版本检查，不会绕过原始签名、架构或进程检查。其他版本的私有接口兼容性尚未确定。

## 安装

下载或克隆源码，在仓库目录打开终端：

```sh
git clone https://github.com/tlzxsun/PhantomBladeZeroMacShaderOpt.git
cd PhantomBladeZeroMacShaderOpt
sh ./install.sh --bottle "$HOME/Library/Application Support/CrossOver/Bottles/PhantomBlade"
```

将 `PhantomBlade` 换成你的瓶子目录名。这里需要指定包含 `cxbottle.conf` 的**瓶子根目录**，不是 `drive_c`。支持带空格的路径。

如果 CrossOver 不在默认位置：

```sh
sh ./install.sh --app "$HOME/Applications/CrossOver.app" --bottle "/path/to/your/bottle"
```

脚本会在本机编译 hook，备份原文件，对启动器副本做本地临时签名、去掉 Hardened Runtime 标记，再**原子替换原路径中的启动器**。原子替换可以避免直接修改已有的启动器硬链接。不会设置额外的 `BinPath`、`LibPath`，也不使用自制 Wine 启动器。

只管理以下瓶子环境变量：

```ini
"DYLD_INSERT_LIBRARIES" = "${WINEPREFIX}/phantomblade-shader-opt/<install-id>/cache_hook.dylib"
"MCH_CACHE_BYTES" = "2147483648"
"MCH_TARGET_EXE" = "PBZ.exe"
"MCH_LOG" = "${WINEPREFIX}/phantomblade-shader-opt/<install-id>/hook.log"
```

`<install-id>` 由脚本自动生成。`${WINEPREFIX}` 会由 CrossOver 展开，不需要手动替换。已有且不属于本补丁的 `DYLD_INSERT_LIBRARIES` 值会保留。脚本不修改 `FS_CACHE_SIZE` 和 `MTL_SHADER_CACHE_SIZE`。

安装后照常启动 Steam 和游戏，让预编译完成，正常退出游戏后再测试重新启动。安装脚本不会自动启动游戏，也不会清理或合并现有 shader 缓存。

## 检查与恢复

```sh
sh ./doctor.sh
python3 ./scripts/cache_status.py
```

安装脚本和 doctor 会输出日志位置。成功拦截时会看到：

```text
cache-open budget=1073741824 -> 2147483648 result=0 path=.../d3dm/PBZ.exe/shaders.cache/.../functions
```

`loaded` 仅说明动态库已加载。`cache-open` 说明改写后的预算已成功传入底层缓存。`cache_status.py` 的 `active_bytes` 大于 `1073741824`，才能说明索引中的有效条目突破原上限。单看 `.data` 文件大小无法证明这一点，因为文件中可能有未使用空间。检查工具依据的是实测私有索引格式，实时写入未完成或未来格式变化可能影响结果。

退出所有 Wine、Steam 进程后恢复：

```sh
sh ./restore.sh
```

状态和原文件备份保存在仓库、瓶子之外：

```text
~/Library/Application Support/PhantomBladeZeroMacShaderOpt/
```

每份备份还包含独立的 `restore.sh` 和 Python 恢复程序，移动源码仓库后仍可恢复。恢复时会校验备份，拒绝覆盖意外更新后的 CrossOver，并尽量保留安装后修改的其他瓶子配置。如果本项目管理的配置项已被手动修改，会报冲突并停止覆盖。hook 文件、日志和 shader 缓存会保留。

一次只管理一个安装。切换瓶子或更新 CrossOver 前先恢复。若出现 Steam 空白窗口、命令窗口反复闪现或游戏异常，先退出瓶子并恢复原文件，再进一步排查。

## 原理与限制

在已测试的 Metal 版本中，`MTLCompilerFSCache` 先读取 `MTL_SHADER_CACHE_SIZE`，再读取 `FS_CACHE_SIZE`，将结果存进 32 位字段，并将其限制到 `0x40000000`，即 1 GiB。因此单纯把环境变量调大无法突破上限，部分溢出数值反而可能禁用缓存。

hook 拦截私有函数 `fscache_open_with_key`。对于包含 `/d3dm/PBZ.exe/` 或 `/mullion/PBZ.exe/` 的路径，它复制实测长度为 16 字节的选项结构，将其中第一个 64 位预算字段提高到 2 GiB。不会修改 Metal 代码，也不会手动改写缓存内容；原本明确禁用的缓存仍保持禁用。预算针对**每个匹配的 CoreFSCache 实例**，包括实测的函数与库缓存，并非整个游戏缓存目录的总大小上限。

动态库会注入所选瓶子的 Wine 进程，并链接 Metal；只有预算改写按游戏缓存路径过滤，动态库本身并未与 Steam 进程隔离。这是实验方案的兼容性风险。本地临时签名不等于 Apple 公证，会改变原厂签名。脚本不修改系统级 Gatekeeper 或 SIP 设置。

hook 和缓存检查工具依赖单个 macOS 版本中观察到的私有实现细节。macOS、CrossOver 更新后需要重新验证。游戏或驱动变化引起的正常缓存失效仍可能发生。实测游戏日志以 `PBZLOGE1` 开头，本项目不解码该格式，也不声称已解释每一处加载停顿。

## 开发与测试

```sh
sh ./scripts/build.sh
python3 -m unittest discover -s tests -v
```

测试覆盖配置保留与冲突、备份校验、硬链接安全替换，以及有效缓存记录统计。测试只使用临时文件，不会安装到你的真实 CrossOver 中。

参考：[Apple：DYLD 环境变量权限](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.cs.allow-dyld-environment-variables)、[Apple：软件公证](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)、[CrossOver EULA](https://www.codeweavers.com/crossover/eula)。上述缓存上限和二进制格式来自本机分析，并非 Apple 承诺支持的公开接口。
