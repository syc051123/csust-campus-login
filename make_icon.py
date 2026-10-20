# 计科2404-史彦超-202308010427
"""生成 exe 图标：蓝底圆角方块 + 白色 WiFi 信号。

产物：build/icon.ico（含 16~256 多尺寸，供 Windows 各场景取用）
"""

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
OUT = os.path.join(ASSETS, "icon.ico")
S = 1024


def build():
    os.makedirs(ASSETS, exist_ok=True)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角底：深蓝，校园网络的感觉
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.23), fill=(26, 86, 214, 255))

    # WiFi 弧：三段同心弧，从内到外变淡，最后加底部圆点
    cx, cy = S // 2, int(S * 0.74)
    arcs = [
        (int(S * 0.155), int(S * 0.062), 255),
        (int(S * 0.265), int(S * 0.058), 235),
        (int(S * 0.375), int(S * 0.054), 205),
    ]
    for radius, width, alpha in arcs:
        d.arc(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            start=215,
            end=325,
            fill=(255, 255, 255, alpha),
            width=width,
        )

    dot = int(S * 0.042)
    d.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=(255, 255, 255, 255))

    img.save(
        OUT,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print("[OK] 图标已生成:", OUT, "%.1f KB" % (os.path.getsize(OUT) / 1024))

    # 顺手留一张 png 预览，方便看效果
    img.resize((256, 256), Image.LANCZOS).save(os.path.join(ASSETS, "icon-preview.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
