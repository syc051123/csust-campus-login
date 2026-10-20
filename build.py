# 计科2404-史彦超-202308010427
"""把 campus_login.py 打包成可分发的单文件 exe。

用法（在构建虚拟环境里跑）：
    <venv>\\Scripts\\python.exe build.py

产物：dist/校园网一键连接.exe
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "campus_login.py")
DIST = os.path.join(HERE, "dist")
WORK = os.path.join(HERE, "_work")
SPEC = os.path.join(HERE, "_spec")
ICON = os.path.join(HERE, "assets", "icon.ico")
VERSION_FILE = os.path.join(HERE, "version_info.txt")

# exe 名用 ASCII 打包（避免 spec/构建中间件的中文编码坑），成功后再改成中文
BUILD_NAME = "CampusLogin"
FINAL_NAME = "校园网一键连接.exe"


def main():
    if not os.path.isfile(SRC):
        print("[ERROR] 找不到 campus_login.py")
        return 1

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--console",
        "--name", BUILD_NAME,
        "--distpath", DIST,
        "--workpath", WORK,
        "--specpath", SPEC,
        # requests / urllib3 / certifi 会被自动分析，这里只是确保不被漏掉
        "--hidden-import", "requests",
        "--hidden-import", "urllib3",
        SRC,
    ]
    if os.path.isfile(ICON):
        cmd[-1:-1] = ["--icon", ICON]
        print("[INFO] 使用图标:", ICON)
    else:
        print("[INFO] 没有 icon.ico，使用默认图标")

    # 版本资源让属性页像个正经程序，也略微降低杀软的可疑度
    if os.path.isfile(VERSION_FILE):
        cmd[-1:-1] = ["--version-file", VERSION_FILE]
        print("[INFO] 使用版本资源:", VERSION_FILE)

    print("[INFO] 开始打包 ...")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("[ERROR] PyInstaller 失败，退出码", result.returncode)
        return result.returncode

    built = os.path.join(DIST, BUILD_NAME + ".exe")
    if not os.path.isfile(built):
        print("[ERROR] 没有生成", built)
        return 1

    final = os.path.join(DIST, FINAL_NAME)
    if os.path.isfile(final):
        os.remove(final)
    shutil.move(built, final)

    size_mb = os.path.getsize(final) / 1024 / 1024
    print("[OK] 已生成 %s  (%.1f MB)" % (final, size_mb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
