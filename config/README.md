# Kernel module provider

新 recovery 不替换已验证的 boot/vendor_boot/DTBO。v4 bootloader 将现有 vendor_boot platform ramdisk 与 recovery ramdisk 合并；AOSP first_stage_init 在 recovery mode 读取 `/lib/modules/modules.load.recovery`。该文件已经位于匹配的 vendor_boot 中，config 下的副本用于审计，不再次覆盖。

450 个 .ko 和 445 行 load list 均来自目标已匹配的 vendor_boot；kernel-modules.json 保留每个 SHA256。Lineage 两个 load list 单独保留作为比较材料，不取代 OEM 列表。没有删 module、strip 或屏蔽错误。

**关于"引用闭包无缺失"的更正。** 上面这句话此前写作"引用闭包无缺失"，该说法与文件内容不符，已按实测更正：

- 本目录的 450 个 module 中，只有 `cfg80211.ko`（kernel-modules.json:103）与 `mac80211.ko`（:618）属于 Wi-Fi 栈；`modules.load.recovery` 里也只有这两条（:405-406）。
- recovery 需要的另外 7 个模块 —— `cnss_prealloc.ko`、`cnss_utils.ko`、`cnss_plat_ipc_qmi_svc.ko`、`wlan_firmware_service.ko`、`cnss_nl.ko`、`cnss2.ko`、`qca_cld3_kiwi_v2.ko` —— **不在**这 450 个里，它们由挂载后的 `/vendor_dlkm/lib/modules` 提供（recovery.fstab:9 将 vendor_dlkm 声明为 ext4）。
- 因此本目录对 vendor_boot 的引用闭包是完整的，但**对 Wi-Fi 的引用闭包并不完整**，两件事不能混为一谈。

**以上分析自 `dc9122f` 起成为历史，又自本次 Wi-Fi 移植起重新生效。** Wi-Fi 曾整体移除，
现按 README 记录的三处联动（模块 loader 门禁、编译门禁、ramdisk hook）完整恢复，因此：

- 运行时加载列表 `TW_LOAD_VENDOR_MODULES`（BoardConfig.mk）与公开接口
  `AERA_LOAD_VENDOR_MODULES`（aera_astonc.mk）重新设置且必须逐字一致；
  `kernel_module_loader.cpp` 由 `bootable/recovery/Android.mk:369` 重新编译进
  recovery；
- `recovery/root/system/bin/beforemodules.sh` 重新入树：`kernel_module_loader.cpp:314`
  在解析任何模块目录之前运行它（2026-10-23 上游核对仍在，finit_module 改造未移除该挂钩）；
- 本目录这 450 个 module 与 `modules.load.recovery` 仍然只是**审计副本**，
  它们描述的是 vendor_boot provider 的既有状态。设备默认行为（first_stage_init
  读 `/lib/modules/modules.load.recovery`）不受影响；recovery 的 Wi-Fi 模块
  由 loader 从 `/vendor_dlkm/lib/modules` 加载，vendor_boot 只贡献
  cfg80211/mac80211 两项。

保留本节的价值在于记录"vendor_boot 引用闭包完整"与"Wi-Fi 引用闭包不完整"
这两件事必须分开判断——当初正是把它们混为一谈，才推出"补几个模块就能修 Wi-Fi"、
进而被误当成解密故障成因。恢复 Wi-Fi 时，后者由
`beforemodules.sh` + loader 模块列表补齐，CI 的配置门禁与二进制/ramdisk
验证步骤会对三处联动同时断言。

现有能启动的 TWRP ramdisk 没有 .ko，支持该 provider 策略。新镜像仍须检查 first-stage/module/UFS/F2FS/dm/crypto/display/touch/USB 日志；在目标 boot/vendor_boot 改变时重新建立 baseline，不能沿用旧 hash。
