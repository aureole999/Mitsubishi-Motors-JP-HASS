# 日本版 Mitsubishi Motors Home Assistant 集成

这是面向日本新版 **Mitsubishi Motors** App 云端服务的非官方 Home Assistant 自定义集成。

## 已实现

- 日本 IDM 登录、两小时 access token 和轮换 refresh token
- 自动发现账户车辆
- 动力电池电量、充电状态、充电枪连接、剩余充电时间
- 空调状态和当前目标温度
- 远程启动、停止空调

当前启动空调固定发送已经实车验证成功的 **25 °C**。温度调节、门锁、鸣笛、闪灯和远程充电尚未在日本车辆上验证，因此没有照搬 EU 版功能。

## 设计上的保护

- 每 15 分钟（可设 5–120 分钟）的常规 polling 只读云端缓存，不唤醒车辆。
- 只有启动空调时才按官方 App 的顺序发送状态刷新和车辆唤醒。
- START/STOP 控制请求绝不自动重发。若网络中断导致结果未知，Home Assistant 会报错并要求用官方 App 确认。
- 点击开启或关闭后，HA 会立即显示请求的模式；命令失败时立即回退，成功后等待云端状态确认。临时状态最多保留两分钟。
- access token 在到期前 5 分钟刷新；服务每次返回的新 refresh token 都会原子写回 HA 配置。
- 日志不记录密码、token、VIN 或请求/响应正文。

## 安装

发布到 GitHub 后，在 HACS 的 Custom repositories 中加入仓库 URL，类别选择 `Integration`，下载 **Mitsubishi Motors Japan** 并重启 HA。

也可以把 `custom_components/mitsubishi_motors_jp` 整个目录复制到：

```text
/config/custom_components/mitsubishi_motors_jp
```

重启后在“设置 → 设备与服务 → 添加集成”中搜索 **Mitsubishi Motors Japan**。输入日本新版 App 使用的邮箱和密码。这个已验证流程不需要旧版的 4 位 PIN。

安装前也可以在普通 macOS Python 中运行只读连接检查。它只登录并读取缓存状态，不唤醒车辆，也不发送控制命令：

```bash
python3 tools/check_connection.py
```

## 为什么启动较慢

车辆需要通过车载通信模块从休眠状态上线。集成先提交 `refreshVSR` 和 `wakeUpVehicle`，最多等待状态刷新 15 秒；若刷新提前完成就立即继续，15 秒后仍在处理中也会按照官方 App 的实测顺序提交一次空调 START。START 的结果使用自己的 request ID 单独轮询；结果未知时绝不重发。

## 与 EU 版的关系

项目参考了 MIT 许可的 [TomTuTHub/mitsubishi-outlander-phev-eu](https://github.com/TomTuTHub/mitsubishi-outlander-phev-eu) 的 Home Assistant 目录组织方式，但日本版登录、加密、请求签名、字段解析和空调流程都是单独实现。它使用新的域名 `mitsubishi_motors_jp`，不会覆盖现有 `mitsubishi_owner_portal` 集成。

这是与三菱汽车无关的社区项目。云端私有 API 可能随 App 更新而变化，请自行承担使用风险。
