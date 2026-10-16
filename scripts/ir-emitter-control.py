#!/usr/bin/env python3
"""
对照实验：区分「红外补光灯的闪烁」和「房间灯光的闪烁」

背景
----
红外补光灯是**逐帧交替**工作的（奇数帧亮、偶数帧灭）。
但房间里的 LED / 日光灯本身就在 100 Hz 闪烁，低帧率采样时会**混叠**成
逐帧交替，在红外相机上伪装成补光灯 —— 这是个很容易踩的假阳性。

原理
----
    /dev/video0  RGB 相机（带红外截止滤镜）
                 → 看得见房间灯光的可见光闪烁
                 → 看不见红外补光灯（被滤镜挡掉）

    /dev/video2  红外相机（无红外截止滤镜）
                 → 两种闪烁都看得见

判读
----
    RGB 也交替，红外也交替   →  是房间灯光   （假阳性，补光灯可能没亮）
    RGB 稳定，  红外交替     →  是红外补光灯 ✔

用法
----
    python3 ir-emitter-control.py            # 默认 video0 / video2
    python3 ir-emitter-control.py 0 2        # 显式指定节点号

注意：需要摄像头节点可读。多数发行版上 systemd-logind 会给当前登录用户
      授予 uaccess ACL，所以**普通用户即可运行，不需要 root**。

退出码：0 = 判定为红外补光灯在闪；1 = 疑似房间灯光；2 = 无法判定
"""
import sys
import time

try:
    import cv2
    import numpy as np
except ImportError:
    sys.exit("缺少依赖：请安装 python3-opencv（以及 python3-numpy）")

SECONDS = 6.0

# 判定阈值：RGB 侧的交替幅度低于这个值就算「稳定」
RGB_STEADY_ALT = 3.0


def probe(dev, label, seconds=SECONDS):
    """采样一个 V4L2 节点，返回逐帧交替统计"""
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
    if not cap.isOpened():
        print(f"  {label:<18} 打不开 {dev}")
        return None

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)

    # 预热，丢掉开头几帧（自动曝光还在收敛）
    for _ in range(15):
        cap.read()

    vals, t0 = [], time.time()
    while time.time() - t0 < seconds:
        ok, frame = cap.read()
        if not ok:
            continue
        if frame.ndim == 3:          # 彩色节点取第一个通道即可
            frame = frame[:, :, 0]
        vals.append(float(frame.mean()))
    cap.release()

    if not vals:
        print(f"  {label:<18} 没采到帧")
        return None

    a = np.array(vals)
    odd, even = a[0::2].mean(), a[1::2].mean()
    lo, hi = np.percentile(a, 10), np.percentile(a, 90)

    print(f"  {label:<18} {len(vals)/seconds:5.1f} fps | "
          f"奇数帧={odd:7.2f}  偶数帧={even:7.2f}  "
          f"交替差={abs(odd-even):6.2f} | "
          f"10~90%跨度={(hi-lo)/(lo+1e-6)*100:5.1f}%")

    return dict(fps=len(vals) / seconds, alt=abs(odd - even),
                swing=(hi - lo) / (lo + 1e-6) * 100)


def main():
    rgb_dev = f"/dev/video{int(sys.argv[1]) if len(sys.argv) > 1 else 0}"
    ir_dev = f"/dev/video{int(sys.argv[2]) if len(sys.argv) > 2 else 2}"

    print("=" * 60)
    print("  对照实验：红外补光灯 vs 房间灯光")
    print("=" * 60)
    print()
    print(f"  RGB 相机 : {rgb_dev}   （有红外截止滤镜 → 只看房间灯光）")
    print(f"  红外相机 : {ir_dev}   （无红外截止滤镜 → 两者都看得到）")
    print()
    print("  ⚠ 测试期间请不要动，也不要开关房间灯")
    print()

    ir = probe(ir_dev, "红外相机")
    rgb = probe(rgb_dev, "RGB 相机")
    print()

    print("=" * 60)
    print("  判读")
    print("=" * 60)

    if ir is None or rgb is None:
        print("  采样不完整，无法判定")
        return 2

    if rgb["alt"] > max(RGB_STEADY_ALT, ir["alt"] * 0.5):
        print("  ⚠ RGB 相机也在交替闪烁  →  这是【房间灯光】的闪烁")
        print("     红外侧的交替很可能是混叠产生的假阳性！")
        return 1

    if ir["alt"] < 3.0:
        print("  ⚠ 红外相机侧也没有交替  →  补光灯没有在工作")
        return 1

    print("  ✔ RGB 相机稳定  →  红外侧看到的交替来自【红外补光灯】")
    print("     结论：补光灯确实在逐帧交替点亮")
    print()
    print(f"     RGB 交替差 {rgb['alt']:.2f}   红外交替差 {ir['alt']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
