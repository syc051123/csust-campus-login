# 长沙理工 · 校园网一键连接

<p align="center">
  <img src="assets/icon.ico" width="96" alt="icon">
</p>

长沙理工大学校园网（Dr.COM eportal）自动认证工具。双击即连，失败自动兜底，支持开机自启。

> **非学校官方项目。** 这是学生自制的辅助脚本，调用的是学校提供的正常认证接口，
> 只是把「打开网页 → 输账号 → 点登录」这一步自动化了。

## 功能

- **一键连接** —— 检测网络状态，已在线直接退出；未认证则自动登录
- **双接口兜底** —— 新版 `:802/eportal/portal/login`（JSONP）失败后自动切换老版 `:801/eportal/`
- **处理抢占登陆** —— 账号在别的设备在线时网关首次返回「抢占登陆」，自动重试
- **WiFi 自动切换** —— 仅在扫描到 `csust-*` 信号时才切；附近没有校园网信号时不动当前连接
- **代理共存** —— 认证请求绕过系统代理，开着 Clash 也能连；能识别 TUN 网卡并提示
- **掉线重连** —— `--daemon` 模式每 5 分钟探测，连续 15 分钟无网则自动退出
- **开机自启** —— 写 HKCU Run 键，不需要管理员权限

## 下载使用

到 [Releases](../../releases/latest) 下载 zip 包（`csust-campus-login-v1.0.0.zip`），解压后双击 `校园网一键连接.exe`。

首次运行会引导填写学号、密码和服务序号（默认 `0` = 校园网，宽带账号填 `1`/`2`/`3`）。
账号只保存在本机 `config.json`，不会上传到任何服务器。

详细步骤见 [使用说明.txt](使用说明.txt)。

## 从源码运行

```bash
pip install requests

python campus_login.py            # 一键连接（首次运行会自动引导配置）
python campus_login.py --setup    # 只改账号
python campus_login.py --test     # 只探测网络状态，不登录
python campus_login.py --daemon   # 常驻守护，掉线自动重连
```

需要 Python 3.8+ 和 `requests`。

## 打包成 exe

```bash
pip install requests pyinstaller pillow

python make_icon.py     # 生成 assets/icon.ico
python build.py         # 产出 dist/校园网一键连接.exe
```

构建细节见 [build.py](build.py)。三个容易踩的坑已经处理好了：

1. **配置路径** —— 单文件 exe 下 `__file__` 指向临时解压目录，必须用 `sys.executable` 的目录，否则每次运行都丢账号
2. **控制台编码** —— exe 没有 bat 里的 `chcp 65001`，要主动调 `SetConsoleOutputCP(65001)`，否则中文乱码
3. **getpass** —— 在非 tty 环境（管道、重定向）下会永久阻塞，需先用 `sys.stdin.isatty()` 判断

## 开机自启

```bash
校园网一键连接.exe --enable-autostart    # 开启
校园网一键连接.exe --disable-autostart   # 关闭
```

开启后**不要移动 exe 的位置**，注册表里存的是绝对路径。

## 工作原理

1. 请求 `http://connect.rom.miui.com/generate_204`，未认证时网关会 302 到认证页
2. 从跳转 URL 中取出 `wlanuserip` / `wlanusermac` / `wlanacip` / `wlanacname`
3. 携带这些参数请求 eportal 登录接口，账号格式为 `,{服务序号},{学号}`
4. 登录成功后再探测一次 204，确认外网真的通了

域名解析不可用时（TUN 代理常见），改用 IP 直连 + `Host` 头，整条链路绕过 DNS。

## 已知限制

- 只适用于本校校园网环境（`csust-*` 的 WiFi）
- 认证接口依赖学校网关版本，学校升级后可能失配，需要重新适配
- 一个账号同时只能在线一台设备，后登录的会顶掉先登录的

## 免责声明

本项目仅供学习交流，使用者需自行承担使用风险。请遵守学校网络使用规定，不要用于任何违规用途。
作者与学校无关，不对因使用本工具导致的任何问题负责。

## License

[MIT](LICENSE)
