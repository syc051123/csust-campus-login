#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长沙理工大学校园网自动登录脚本 (login.csust.edu.cn / Dr.COM eportal)

用法(平时不用管, 双击"校园网一键连接.exe"即可):
    无参数            # 一键连接: 检测 -> (必要时)切校园网WiFi -> 登录
    --setup           # 重新填写学号密码(写入 config.json)
    --test            # 只检测当前网络状态, 不登录
    --daemon          # 常驻守护, 掉线自动重连(供开机自启使用)
    --enable-autostart / --disable-autostart   # 开启 / 关闭开机自动登录

开机自启: 注册表 HKCU Run 键 "CampusNetAutoLogin", 指向本程序 --once。
在校: 自动登录; 在热点/校外: 检测后退出, 不改动当前连接。
配置与日志: 与程序同目录 (config.json / login.log), 不写注册表以外的地方。
"""

import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def _resolve_base_dir():
    """配置和日志放在 exe 旁边。

    打包成单文件 exe 后, __file__ 指向每次启动都会重建的临时解压目录,
    照着它写配置等于每次运行都丢账号 —— 所以必须改用 sys.executable 的目录。
    如果 exe 放在 Program Files 这类没有写权限的地方, 就退到 %APPDATA%。
    """
    if getattr(sys, "frozen", False):
        home = os.path.dirname(os.path.abspath(sys.executable))
    else:
        home = os.path.dirname(os.path.abspath(__file__))
    try:
        probe = os.path.join(home, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return home
    except OSError:
        fallback = os.path.join(
            os.environ.get("APPDATA") or os.path.expanduser("~"), "CampusNetLogin"
        )
        os.makedirs(fallback, exist_ok=True)
        return fallback


BASE_DIR = _resolve_base_dir()
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "login.log")

# 探测地址: 校园网未登录时, 任何 HTTP 请求都会被网关 302 劫持到认证页
CHECK_URL = "http://connect.rom.miui.com/generate_204"
# 兜底探测: 校园网未认证时 DNS 往往不可用(开着 Clash 之类代理软件的 TUN 模式时
# 尤其明显, 域名解析会整个失败)。这时改用已知 IP 直连, 靠 Host 头让网关认出
# 目标域名 —— 网关是按"目标不在内网"来劫持的, 所以直连 IP 一样会被 302,
# 但整条链路不再需要 DNS。
CHECK_FALLBACK = [
    ("connect.rom.miui.com", "220.181.104.183"),
    ("connect.rom.miui.com", "202.69.4.32"),
]
# 认证服务器解析失败时的备用 IP
FALLBACK_PORTAL_IP = "192.168.7.221"
PORTAL_DOMAIN = "login.csust.edu.cn"
# 服务序号: 0=校园网默认; 如果账号绑定的是电信/联通/移动宽带, 登录报
# "运营商标记错误"之类提示时, 在 config.json 里把 service_index 改成 1/2/3 试试
DEFAULT_SERVICE_INDEX = 0

# 开机等待窗口: WiFi 连接 + DHCP 需要时间, 最多探测 24 次 x 5 秒 = 2 分钟
MAX_WAIT_ROUNDS = 24
WAIT_SECONDS = 5


def log(msg):
    line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            # 超过 1MB 就重来, 防止日志无限变大
            if f.tell() > 1024 * 1024:
                open(LOG_PATH, "w", encoding="utf-8").close()
                f.write("")
            f.write(line + "\n")
    except OSError:
        pass
    try:
        print(line)
    except Exception:
        pass


def get_wifi_ssid():
    """返回当前连接的 WiFi 名字, 拿不到(有线/没连)返回 None"""
    try:
        out = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, timeout=5,
        ).stdout.decode("gbk", errors="replace")
    except Exception:
        return None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("SSID") and ":" in s:
            return s.split(":", 1)[1].strip() or None
    return None


def detect_tun_proxy():
    """看看有没有代理软件的虚拟网卡在接管流量。

    这类 TUN 网卡(Clash / Mihomo / sing-box ...)会抢走默认路由, 校园网未认证时
    会把探测请求连同 DNS 一起带进死胡同 —— 现象就是"怎么都连不上认证页"。
    """
    try:
        out = subprocess.run(
            ["netsh", "interface", "show", "interface"],
            capture_output=True, timeout=8,
        ).stdout.decode("gbk", errors="replace")
    except Exception:
        return None
    low = out.lower()
    for key in ("clash", "mihomo", "sing-box", "v2ray", "wireguard", "tun"):
        if key in low:
            return key
    return None


def describe_online():
    """已联网时的提示文案: 说清楚连的是校园网还是别的网"""
    ssid = get_wifi_ssid()
    if ssid and "csust" in ssid.lower():
        return "校园网(%s)已在线, 无需登录" % ssid
    if ssid:
        return "当前网络已能上网(WiFi: %s), 无需登录校园网" % ssid
    return "当前网络已能上网, 无需登录校园网"


# 校园网 WiFi 按 SSID 关键字识别(不区分大小写); 这里按优先级排序,
# 多个信号同时存在时选排前面的 (csust-student 是现在的主力网)
CAMPUS_SSID_KEYWORD = "csust"
CAMPUS_SSID_PRIORITY = ["csust-student"]


def _netsh_wlan(args):
    """跑 netsh wlan 命令, 返回 GBK 解码后的输出 (netsh 输出是 GBK 编码)"""
    try:
        r = subprocess.run(
            ["netsh", "wlan"] + args, capture_output=True, timeout=15,
        )
        return r.stdout.decode("gbk", errors="replace")
    except Exception:
        return ""


def list_saved_profiles():
    """已保存的 WiFi 配置名列表"""
    names = []
    for line in _netsh_wlan(["show", "profiles"]).splitlines():
        # 中文系统: "所有用户配置文件 : csust-dx" / 英文: "All User Profile : xxx"
        if (":" in line) and ("配置文件" in line or "Profile" in line):
            names.append(line.split(":", 1)[1].strip())
    return names


def scan_campus_ssids():
    """扫描附近信号里的校园网 WiFi"""
    ssids = []
    for line in _netsh_wlan(["show", "networks"]).splitlines():
        s = line.strip()
        if s.startswith("SSID") and ":" in s:
            name = s.split(":", 1)[1].strip()
            if name and CAMPUS_SSID_KEYWORD in name.lower():
                ssids.append(name)
    # 去重保序
    return list(dict.fromkeys(ssids))


def switch_to_campus():
    """确保连上校园网 WiFi。已连则直接返回; 否则扫描附近信号, 找到才切换。
    附近没有校园网信号时绝不断开当前连接(防止两边落空)。"""
    ssid_now = get_wifi_ssid()
    if ssid_now and CAMPUS_SSID_KEYWORD in ssid_now.lower():
        return True

    campus_ssids = scan_campus_ssids()
    if not campus_ssids:
        log("附近未发现校园网WiFi(%s-*), 保持当前连接[%s]不动" % (CAMPUS_SSID_KEYWORD, ssid_now))
        return False

    profiles = [p for p in list_saved_profiles() if CAMPUS_SSID_KEYWORD in p.lower()]

    def sort_key(s):
        low = s.lower()
        for i, p in enumerate(CAMPUS_SSID_PRIORITY):
            if p in low:
                return (0, i)
        return (1, 0)

    ordered = sorted(campus_ssids, key=sort_key)
    # 优先选"已保存过配置"的, 这样不用重新输WiFi密码
    target = next((s for s in ordered if s in profiles), ordered[0])
    log("发现校园网WiFi: %s (已保存配置: %s), 从[%s]切换过去" % (target, target in profiles, ssid_now))
    subprocess.run(["netsh", "wlan", "connect", "name=%s" % target], timeout=15)
    for _ in range(10):  # 最多等 20 秒
        time.sleep(2)
        cur = get_wifi_ssid()
        if cur and CAMPUS_SSID_KEYWORD in cur.lower():
            log("已连接到 %s" % cur)
            return True
    log("切换WiFi超时, 请手动连接校园网")
    return False


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    username = str(cfg.get("username", "")).strip()
    # 支持两种填法: 明文 "password" 字段直接填; 或 --setup 生成的 "password_base64"
    password = str(cfg.get("password", "")).strip()
    if not password:
        try:
            password = base64.b64decode(str(cfg.get("password_base64", "")).strip()).decode("utf-8")
        except Exception:
            password = ""
    if not username or not password:
        return None
    service = cfg.get("service_index", DEFAULT_SERVICE_INDEX)
    return {"username": username, "password": password, "service_index": service}


def save_config(username, password, service_index):
    cfg = {
        "username": username,
        # base64 只是防瞄一眼, 不是加密; 文件只存在本机
        "password_base64": base64.b64encode(password.encode("utf-8")).decode("ascii"),
        "service_index": service_index,
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def new_session():
    s = requests.Session()
    s.trust_env = False  # 忽略系统/环境变量里的代理设置, 防止被本地代理截胡
    s.verify = False
    return s


def _probe(url, host_header=None, timeout=5):
    """发一个不跟随重定向的 GET, 返回 (response, 错误原因)。"""
    headers = {"Host": host_header} if host_header else None
    try:
        r = new_session().get(url, headers=headers, allow_redirects=False,
                              timeout=timeout)
        return r, None
    except requests.RequestException as e:
        return None, str(e)


def _classify(r):
    """把探测响应归类; 命中返回 (status, params, portal_host), 否则返回三个 None。"""
    if r.status_code == 204:
        return "online", None, None

    location = r.headers.get("Location", "")
    if 300 <= r.status_code < 400 and location:
        parsed = urlparse(location)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if "wlanuserip" in params:
            return "portal", params, parsed.hostname
    return None, None, None


def check_network(timeout=5):
    """探测网络状态。
    返回 (status, params, detail):
      status = "online"  已联网
      status = "portal"  被校园网劫持, params 里带着 wlanuserip 等登录参数
      status = "offline" 完全不通 (WiFi 没连上 / 校外没网),
                         detail 是最后一次的失败原因, 方便排查
    """
    problems = []

    r, err = _probe(CHECK_URL, timeout=timeout)
    if r is None:
        problems.append("域名探测: " + err)
    else:
        status, params, portal_host = _classify(r)
        if status:
            return status, params, portal_host
        problems.append("域名探测返回 HTTP %d" % r.status_code)

    # 域名这一步不通时换 IP 直连再试: 不需要 DNS, 未认证时照样会被网关 302 到认证页
    for host, ip in CHECK_FALLBACK:
        r, err = _probe("http://%s/generate_204" % ip,
                        host_header=host, timeout=timeout)
        if r is None:
            problems.append("直连 %s: %s" % (ip, err))
            continue
        status, params, portal_host = _classify(r)
        if status:
            return status, params, portal_host
        problems.append("直连 %s 返回 HTTP %d" % (ip, r.status_code))

    return "offline", None, " / ".join(problems[-2:])


def resolve_portal_ip(portal_host):
    """把认证页域名解析成内网 IP; 解析不出来就用已知备用 IP。"""
    for host in (portal_host, PORTAL_DOMAIN):
        if not host:
            continue
        try:
            ip = socket.gethostbyname(host)
            if ip and not ip.startswith(("127.",)):
                return ip
        except OSError:
            continue
    return FALLBACK_PORTAL_IP


def login_new_api(cfg, params, portal_ip):
    """新版接口: https://login.csust.edu.cn:802/eportal/portal/login (GET + JSONP)"""
    qs = {
        "callback": "dr1004",
        "login_method": 1,
        "user_account": ",%s,%s" % (cfg["service_index"], cfg["username"]),
        "user_password": cfg["password"],
        "wlan_user_ip": params.get("wlanuserip", ""),
        "wlan_user_mac": params.get("wlanusermac", ""),
        "wlan_ac_ip": params.get("wlanacip", ""),
        "wlan_ac_name": params.get("wlanacname", ""),
        "jsVersion": "4.2.1",
        "terminal_type": 1,
        "lang": "zh",
        "v": int(time.time()),
    }
    r = new_session().get(
        "https://%s:802/eportal/portal/login" % portal_ip,
        params=qs,
        headers={"Host": "%s:802" % PORTAL_DOMAIN},
        timeout=10,
    )
    m = re.match(r"^\w+\((.*)\);?\s*$", r.text.strip(), re.S)
    if not m:
        return False, "响应格式异常: " + r.text[:120]
    data = json.loads(m.group(1))
    return data.get("result") == 1, str(data.get("msg", ""))


def login_old_api(cfg, params, portal_ip):
    """老版接口(备胎): http://认证IP:801/eportal/ (POST)"""
    post_url = (
        "http://%s:801/eportal/?c=ACSetting&a=Login&protocol=http:&hostname=%s"
        "&iTermType=1&wlanuserip=%s&wlanacip=%s&wlanacname=%s&mac=%s&ip=%s"
        "&enAdvert=0&queryACIP=0&loginMethod=1"
        % (
            portal_ip,
            portal_ip,
            params.get("wlanuserip", ""),
            params.get("wlanacip", ""),
            params.get("wlanacname", ""),
            params.get("wlanusermac", ""),
            params.get("wlanuserip", ""),
        )
    )
    data = {
        "DDDDD": ",%s,%s" % (cfg["service_index"], cfg["username"]),
        "upass": cfg["password"],
        "0MKKey": "123456",
        "R1": "0", "R2": "0", "R3": "0", "R6": "0",
        "para": "00",
    }
    r = new_session().post(post_url, data=data, timeout=10)
    return "成功" in r.text or "success" in r.text.lower(), r.text[:120]


def try_login(cfg, params, portal_host):
    portal_ip = resolve_portal_ip(portal_host)
    log("认证服务器 IP: %s, 登录账号: %s" % (portal_ip, cfg["username"]))

    # 新版接口: 账号在别的设备在线时, 第一次请求返回"抢占登陆"(顶号下线),
    # 紧接着再请求一次就成功, 所以这里最多重试 3 次
    for attempt in range(1, 4):
        try:
            ok, msg = login_new_api(cfg, params, portal_ip)
        except Exception as e:
            ok, msg = False, str(e)
        log("新版(802)接口第%d次: %s %s" % (attempt, "成功" if ok else "失败", msg))
        if ok:
            time.sleep(2)
            status, _, _ = check_network()
            if status == "online":
                log("确认已联网, 登录完成")
                return True
            log("接口返回成功但外网仍不通, 继续尝试")
            continue
        if ("抢占" in msg) or ("再次登" in msg):
            time.sleep(1)
            continue
        break  # 其他错误(密码错/欠费等)重试也没用, 换老接口兜底

    try:
        ok, msg = login_old_api(cfg, params, portal_ip)
    except Exception as e:
        ok, msg = False, str(e)
    log("老版(801)接口: %s %s" % ("成功" if ok else "失败", msg))
    if ok:
        time.sleep(2)
        status, _, _ = check_network()
        if status == "online":
            log("确认已联网, 登录完成")
            return True
    return False


def ensure_login(max_rounds=MAX_WAIT_ROUNDS):
    """探测网络并在需要时登录。
    返回 (state, cfg): state 为 already_online / login_ok / login_fail / timeout
    """
    detail = ""
    for i in range(1, max_rounds + 1):
        status, params, detail = check_network()
        if status == "online":
            log("已联网, 无需登录" if i == 1 else "第%d次探测: 已联网" % i)
            return "already_online", None
        if status == "portal":
            log("检测到校园网认证页(第%d次探测), 开始登录" % i)
            cfg = load_config()
            if cfg is None:
                log("未配置账号! 请运行: python %s --setup" % os.path.basename(__file__))
                return "login_fail", None
            return ("login_ok" if try_login(cfg, params, detail) else "login_fail"), cfg
        if i == 1:
            tun = detect_tun_proxy()
            if tun:
                log("检测到 %s 虚拟网卡在接管流量, 未认证时它可能拖累探测和 DNS" % tun)
            log("网络未就绪(WiFi 可能还在连接), %d 秒后重试..." % WAIT_SECONDS)
        time.sleep(WAIT_SECONDS)
    log("等待超时: 未检测到校园网认证页, 可能不在校园网环境 [%s]" % detail)
    return "timeout", None


def run():
    # 开机场景: 连着热点且附近有校园网信号时, 自动切到校园网
    if not switch_to_campus():
        return 1  # 附近没有校园网(在家/热点), 不做任何事
    state, cfg = ensure_login()
    if state == "login_ok":
        # 登录成功后常驻守护, 掉线自动重连
        keepalive(cfg)
    return 0 if state in ("already_online", "login_ok") else 1


def keepalive(cfg):
    """每 5 分钟探测一次: 掉线就重新登录; 连续 3 次完全断网(离开校园/关机)则退出。"""
    offline_count = 0
    while True:
        time.sleep(300)
        status, params, portal_host = check_network()
        if status == "online":
            offline_count = 0
            continue
        if status == "portal":
            log("检测到掉线, 自动重新登录")
            try_login(cfg, params, portal_host)
            offline_count = 0
            continue
        offline_count += 1
        log("网络不通(%d/3)" % offline_count)
        if offline_count >= 3:
            log("连续 15 分钟无网络, 判定已不在校园网, 守护退出")
            return


def setup():
    print("== 长沙理工校园网自动登录 - 账号配置 ==")
    username = input("学号(工号): ").strip()
    import getpass
    # 只有在真正的控制台里才隐藏输入。
    # 被别的程序用管道/重定向启动时, getpass 会一直等控制台而不读管道,
    # 表现为程序卡死不动 —— 所以先看 stdin 是不是终端, 不是就退回普通读取。
    try:
        is_tty = sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        is_tty = False
    if is_tty:
        password = getpass.getpass("密码(输入不回显): ").strip()
    else:
        password = input("密码(会直接显示): ").strip()
    service = input("服务序号[默认0=校园网, 电信宽带填1]: ").strip() or "0"
    if not username or not password:
        print("学号或密码为空, 未保存")
        return 1
    save_config(username, password, service)
    print("已保存到", CONFIG_PATH)
    return 0


def once():
    """手动一键连接: 断热点 -> 连校园网 -> 登录, 一条龙。
    返回退出码 (0 = 已在线或登录成功)。"""
    # 首次运行没有账号时, 直接在这里引导填写。
    # 分发给同学后没人会去命令行敲 --setup, 所以必须自动接上。
    if load_config() is None:
        print("=" * 42)
        print("  首次使用, 需要先填一次你的校园网账号")
        print("  只保存在本机的 config.json 里, 不会上传到任何地方")
        print("=" * 42)
        print()
        if setup() != 0:
            print("未完成配置, 已退出")
            return 1
        print()

    status, _, _ = check_network()
    if status == "online":
        ssid = get_wifi_ssid()
        if ssid and CAMPUS_SSID_KEYWORD in ssid.lower():
            print("  [OK] " + describe_online() + " , 本窗口可以关闭了")
            print("=" * 42)
            return 0
        print("当前连着[%s], 尝试切换到校园网..." % (ssid or "其他网络"))
        if not switch_to_campus():
            print("  [提示] 附近没有发现校园网WiFi(%s-*), 未改动当前连接" % CAMPUS_SSID_KEYWORD)
            print("=" * 42)
            return 0
    else:
        # 完全不通时先主动连一次校园网 WiFi。
        # 原逻辑在这种情况下只是干等 ensure_login, 用户看到的是一分钟没反应。
        switch_to_campus()

    # 开机后 WiFi 连接 + DHCP + 认证页就绪可能要好一会儿,
    # 12 轮 x 5 秒 = 1 分钟, 比原来的 30 秒宽裕些
    state, _ = ensure_login(max_rounds=12)
    print()
    print("=" * 42)
    if state in ("already_online", "login_ok"):
        msg = describe_online() if state == "already_online" else "校园网登录成功"
        print("  [OK] %s , 本窗口可以关闭了" % msg)
        print("=" * 42)
        return 0
    print("  [失败] 未能联网, 具体原因看上方日志;")
    print("  可把 login.log 发给 AI 排查")
    print("=" * 42)
    return 1


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "CampusNetAutoLogin"


def autostart(enable):
    """把本程序写进 HKCU 的 Run 键 (或移除), 实现开机自动连接。
    用 HKCU 而不是 HKLM, 不需要管理员权限。"""
    try:
        import winreg
    except ImportError:
        print("[失败] 当前系统没有 winreg, 不支持开机自启")
        return 1

    if getattr(sys, "frozen", False):
        command = '"%s" --once' % os.path.abspath(sys.executable)
    else:
        command = '"%s" "%s" --once' % (sys.executable, os.path.abspath(__file__))

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enable:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)
                print("[OK] 已开启开机自动登录")
                print("     开机时会闪一个黑窗口, 连上后几秒自动关闭, 正常现象")
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                    print("[OK] 已关闭开机自动登录")
                except FileNotFoundError:
                    print("[提示] 本来就没有开启开机自启")
    except OSError as e:
        print("[失败] 写注册表失败:", e)
        return 1
    return 0


def _prepare_console():
    """exe 双击启动时控制台默认是 GBK 代码页, 直接打印中文会乱码。
    源码版靠 bat 里的 chcp 65001 解决, exe 没有 bat, 所以自己切一次。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass


if __name__ == "__main__":
    _prepare_console()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = sys.argv[1:]
    # 双击启动(无参数)时窗口会随进程消失, 出错信息一闪而过, 所以结束时停一下
    interactive = getattr(sys, "frozen", False) and not args
    code = 0
    try:
        if "--setup" in args:
            code = setup()
        elif "--test" in args:
            st, pm, ph = check_network()
            print("网络状态:", st, "| 认证参数:", pm, "| 认证页:", ph)
        elif "--enable-autostart" in args:
            code = autostart(True)
        elif "--disable-autostart" in args:
            code = autostart(False)
        elif "--daemon" in args or "--service" in args:
            # 常驻守护: 掉线自动重连, 主要给开机自启用
            code = run()
        else:
            # 无参数 (双击) 一律按"一键连接"处理
            code = once()
    except (EOFError, KeyboardInterrupt):
        # 用户直接关窗口或在半途按了 Ctrl+C, 不该甩一堆 traceback
        print()
        print("[已取消] 没有收到输入, 账号未保存")
        code = 1
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    finally:
        if interactive:
            try:
                input("\n按回车键关闭窗口...")
            except (EOFError, KeyboardInterrupt):
                pass
    sys.exit(code)
