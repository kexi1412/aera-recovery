# AERA device tree for OnePlus Ace 3

国行 OnePlus Ace 3：PJE110 / OP5CF9L1，`astonc`，SM8550 / kalama，arm64。
基于 AERA Recovery `aera-16.0`（Android 16 userspace），目标为已实测的
ColorOS Android 17 移植 ROM。此仓库是独立设备树，应放在
`device/oneplus/astonc`。

本树从已验证的 OrangeFox `astonc` 设备树移植而来：布局与硬件配置沿用
原厂 smali/实测得到的参数，仅把公开配置从 `OF_*`/`FOX_*`/`TW_*` 改名为
AERA 的 `AERA_*` 命名空间，并改用 AERA 的原生 LVGL 界面。AERA 后台仍读
`OF_*`/`TW_*` 内部名，由 AERA 共享构建系统的 `aera_config.mk`（在
`bootable/recovery`，由 AERA 版本 `vendor/twrp/config/common.mk` 侧引入）
完成 `AERA_*` 到内部名的翻译，因此设备树对外只出现 `AERA_*`。
这不是官方 AERA 发布。

## 已验证与待验证

OrangeFox build-14 通过启动/稳定性、显示/触摸/ADB、PIN 解密、metadata/DE/CE、
内部存储读写与文件管理、init_boot 备份及摘要核对、真实电量/充电标记、
日期/北京时间、状态栏、终端/键盘、息屏唤醒、主题/语言/设置持久化、
fastbootd 只读查询及返回、Sideload 连接/取消返回。
亮度、CPU 显示、手电筒和 KernelSU 模块管理有 build-10 的独立验收记录。

**本 AERA 树尚未在真机验收。** 移植只改了变量命名与界面后端，硬件相关
配置（fstab、crypto、sepolicy、init rc、relink 库表）逐字保留；但 AERA
自带 WPE 界面、联网、云端备份等组件，且其启动期与 OrangeFox 不同，
需重新跑一遍启动/显示/触摸/解密/备份验收，不能直接沿用 OrangeFox 结论。
压缩备份引擎、Recovery 界面锁、其他 Android 锁屏类型仍未通过验收；
MTP 当前关闭；震动无效，用户接受暂缓。SELinux 保留上游/debug 的八个
permissive domains，尚不是全域 enforcing。

## 编译

设备使用独立、100 MiB、v4/LZ4、无内核的 recovery 镜像；复用已匹配
boot/vendor_boot 的 kernel ABI。A/B、dynamic partitions、F2FS、wrapped
metadata encryption 配置来自目标设备。

```sh
git clone --branch aera-16.0 https://github.com/AERA-Recovery/android_manifest.git manifest
# 按 manifest README 完成 repo init/sync（需要 AERA-Recovery 组织访问权限与 SSH key）
git clone --branch aera-16.0 <本仓库> device/oneplus/astonc
```

仅 clone 此仓库不足以编译。AERA 需要其私有 manifest（`aera-16.0`）提供的
`vendor/twrp`、`external/lvgl`、`system/core` 等 AERA 定制仓库，以及相同 ROM
的私有 hardware/crypto 依赖。本仓库不包含 OEM 镜像、proprietary binary、
设备日志或密钥。私有导入资产的位置和 SHA256 在 `config/` 清单中。

完成源码同步和私有资产导入后，编译入口为：

```sh
export ALLOW_MISSING_DEPENDENCIES=true
source device/oneplus/astonc/vendorsetup.sh
source build/envsetup.sh
lunch twrp_astonc-bp2a-eng
mka adbd recoveryimage
```

产物为 `out/target/product/astonc/recovery.img`。保留常规 `twrp_astonc`
product 名称；AERA 与 OrangeFox 一样，product 前缀仍是 `twrp_`，设备树通过
`aera_astonc.mk` 声明 AERA 专属开关。

## AERA 移植要点

- **命名空间**：所有公开开关改为 `AERA_*`（见 BoardConfig.mk / vendorsetup.sh /
  aera_astonc.mk）。`AERA_MAINTAINER_PATCH_VERSION` 不再设置（AERA 起于 R1.0）。
- **原生界面**：`recovery/root/system/etc/recovery-ui2.enabled` 标记启用 AERA
  LVGL 界面；`AERA_UI_ADAPTIVE_RESOLUTION := true` 让其把 1440x3168 设计面
  缩放到本机 1264x2780 面板。`AERA_THEME := portrait_hdpi` 仍保留，作为
  AERA 主题/布局族的选型（与 dodge 参考树一致）；界面本身由 LVGL 绘制。
- **主题/状态栏几何**：`AERA_SCREEN_H/STATUS_H/STATUS_INDENT_*` 沿用实测挖孔
  参数（hole y=40..112，center 76）。
- **已移除的 OrangeFox 开关**：AERA 不再有 AROMA 文件管理器、updatezip、
  Misans 字体、自定义 bins-to-sdcard 等选项，对应 `FOX_*` 行已删除。
- **保留的自有栈**：`init.recovery.qcom.rc`、`astonc.security.rc`、
  gatekeeper/lpdumpd rc、`sepolicy/`、`config/` 与 BoardConfig 的 relink 库表
  逐字保留（本机已验证的 QTI 解密路径）。与 AERA 通用 `device/qcom/twrp-common`
  qcom_decrypt 并存时注意 service 命名冲突。

## 维护与来源

**维护者：kexi1412**（`kexi1412/aera-recovery`）。本树由本仓库独立维护，
不走上游设备树的分支或 PR 流程；改动直接落在本仓库 `main`。
界面 About 页的 MAINTAINER 行即此署名（`AERA_MAINTAINER`）。

配置基线来源（仅指硬件参数与已实测数据取自哪里，与维护权无关）：

- AERA official `aera-16.0`（框架、构建系统、LVGL 界面后端）
- 已验证的 OrangeFox `astonc` 树（`rkbkosp/astonc-orangefox`）—— fstab、
  crypto、sepolicy、init rc、relink 库表的原始出处
- LineageOS astonc / sm8550-common `lineage-23.2`
- 目标 stock/current 输入（见 `PROVENANCE.json` 的 commit 与 SHA256 记录）

`PROVENANCE.json` 的 `source_*` 字段指的就是上述第二项，属于可追溯性记录，
不是对本仓库归属的声明。

目标 ROM、boot/vendor_boot ABI 或 security patch 改变时重新收集 baseline；
不要替换旧 blobs、删除 FBE 参数或伪造 security patch 来绕过故障。
