# Kernel module provider

新 recovery 不替换已验证的 boot/vendor_boot/DTBO。v4 bootloader 将现有 vendor_boot platform ramdisk 与 recovery ramdisk 合并；AOSP first_stage_init 在 recovery mode 读取 `/lib/modules/modules.load.recovery`。该文件已经位于匹配的 vendor_boot 中，config 下的副本用于审计，不再次覆盖。

450 个 .ko 和 445 行 load list 均来自目标已匹配的 vendor_boot；kernel-modules.json 保留每个 SHA256。Lineage 两个 load list 单独保留作为比较材料，不取代 OEM 列表。没有删 module、strip 或屏蔽错误。

**关于"引用闭包无缺失"的更正。** 上面这句话此前写作"引用闭包无缺失"，该说法与文件内容不符，已按实测更正：

- 本目录的 450 个 module 中，只有 `cfg80211.ko`（kernel-modules.json:103）与 `mac80211.ko`（:618）属于 Wi-Fi 栈；`modules.load.recovery` 里也只有这两条（:405-406）。
- recovery 需要的另外 7 个模块 —— `cnss_prealloc.ko`、`cnss_utils.ko`、`cnss_plat_ipc_qmi_svc.ko`、`wlan_firmware_service.ko`、`cnss_nl.ko`、`cnss2.ko`、`qca_cld3_kiwi_v2.ko` —— **不在**这 450 个里，它们由挂载后的 `/vendor_dlkm/lib/modules` 提供（recovery.fstab:9 将 vendor_dlkm 声明为 ext4）。
- 因此本目录对 vendor_boot 的引用闭包是完整的，但**对 Wi-Fi 的引用闭包并不完整**，两件事不能混为一谈。Wi-Fi 能否起来取决于 `/vendor_dlkm` 被成功挂载并 insmod，这条路径由 `recovery/root/system/bin/beforemodules.sh` 负责，见该文件的说明。

另外，`kernel-modules.json` 与 `modules.load.recovery` 都是**审计副本**，不是运行时的加载依据：运行时真正的加载列表是 recovery 二进制内嵌的 `TW_LOAD_VENDOR_MODULES`（见 `BoardConfig.mk`）加上上面的 hook 脚本。

现有能启动的 TWRP ramdisk 没有 .ko，支持该 provider 策略。新镜像仍须检查 first-stage/module/UFS/F2FS/dm/crypto/display/touch/USB 日志；在目标 boot/vendor_boot 改变时重新建立 baseline，不能沿用旧 hash。
