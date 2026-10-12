# Kernel module provider

新 recovery 不替换已验证的 boot/vendor_boot/DTBO。v4 bootloader 将现有 vendor_boot platform ramdisk 与 recovery ramdisk 合并；AOSP first_stage_init 在 recovery mode 读取 `/lib/modules/modules.load.recovery`。该文件已经位于匹配的 vendor_boot 中，config 下的副本用于审计，不再次覆盖。

450 个 .ko 和 445 行 load list 均来自目标已匹配的 vendor_boot；kernel-modules.json 保留每个 SHA256。Lineage 两个 load list 单独保留作为比较材料，不取代 OEM 列表。没有删 module、strip 或屏蔽错误。

**关于"引用闭包无缺失"的更正。** 上面这句话此前写作"引用闭包无缺失"，该说法与文件内容不符，已按实测更正：

- 本目录的 450 个 module 中，只有 `cfg80211.ko`（kernel-modules.json:103）与 `mac80211.ko`（:618）属于 Wi-Fi 栈；`modules.load.recovery` 里也只有这两条（:405-406）。
- recovery 需要的另外 7 个模块 —— `cnss_prealloc.ko`、`cnss_utils.ko`、`cnss_plat_ipc_qmi_svc.ko`、`wlan_firmware_service.ko`、`cnss_nl.ko`、`cnss2.ko`、`qca_cld3_kiwi_v2.ko` —— **不在**这 450 个里，它们由挂载后的 `/vendor_dlkm/lib/modules` 提供（recovery.fstab:9 将 vendor_dlkm 声明为 ext4）。
- 因此本目录对 vendor_boot 的引用闭包是完整的，但**对 Wi-Fi 的引用闭包并不完整**，两件事不能混为一谈。

**以上分析自 `dc9122f` 起已成为历史。** Wi-Fi 已整体移除（见 `BoardConfig.mk` 的
`Kernel module loading: intentionally NOT configured.` 一节），因此：

- 运行时加载列表 `TW_LOAD_VENDOR_MODULES` 不再设置，`kernel_module_loader.cpp`
  被 `bootable/recovery/Android.mk:362` 整体编译掉；
- `recovery/root/system/bin/beforemodules.sh`（本条此前指向的那个 hook）已删除；
- 本目录这 450 个 module 与 `modules.load.recovery` 仍然只是**审计副本**，
  它们描述的是 vendor_boot provider 的既有状态，**不因本次移除而改变**——
  设备默认行为（first_stage_init 读 `/lib/modules/modules.load.recovery`）
  不受影响，这正是回归真机验证配置 `95e0b23` 的形态。

保留本节的价值在于记录"vendor_boot 引用闭包完整"与"Wi-Fi 引用闭包不完整"
这两件事必须分开判断——当初正是把它们混为一谈，才推出"补几个模块就能修 Wi-Fi"、
进而被误当成解密故障成因。

现有能启动的 TWRP ramdisk 没有 .ko，支持该 provider 策略。新镜像仍须检查 first-stage/module/UFS/F2FS/dm/crypto/display/touch/USB 日志；在目标 boot/vendor_boot 改变时重新建立 baseline，不能沿用旧 hash。
