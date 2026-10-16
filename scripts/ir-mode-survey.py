#!/usr/bin/env python3
"""
红外补光灯模式普查（带屏幕提示的交互式脚本）

依次把 MSXU FACE_AUTHENTICATION 切到模式 0 / 1 / 2 / 3，
每个模式：
    1. 桌面弹通知「现在是 mode=N」
    2. 采样 5 秒并存图
    3. 弹窗问操作者「看到红点了吗？」——点按钮回答

最后打印「哪个模式点亮了红点」的结论表。

为什么需要人来看
----
补光灯的红色光点在**摄像头旁边的机身上**，不在摄像头的视野里，
所以相机永远看不到自己的补光灯。整幅画面亮度的变化又会被自动曝光干扰。
**人眼是这里唯一可靠的判据。**

注意
----
本脚本会**写入**私有 UVC 扩展单元。虽然写的是设备自己声明的
GET_MIN..GET_MAX 范围内的值，但这类 XU 没有公开规范，
盲写有把模块写坏的公开前例。**请先读 README 的「核心发现」，确认真的需要再跑。**

在这台 ThinkPad L14 Gen 1 上实测的结论是：**默认模式本来就正常，不需要写入。**

需要：python3-opencv、notify-send、zenity
"""
import ctypes
import fcntl
import os
import subprocess
import sys
import time

DEVICE = os.environ.get("IR_DEVICE", "/dev/video2")
UNIT = int(os.environ.get("IR_UNIT", "14"))
SELECTOR = 0x06
OUTDIR = os.path.expanduser("~/ir-frames")

UID = os.getuid()
BUS = os.environ.get("DBUS_SESSION_BUS_ADDRESS",
                     f"unix:path=/run/user/{UID}/bus")

UVC_GET_CUR = 0x81
UVC_GET_LEN = 0x85
UVC_GET_INFO = 0x86
UVC_SET_CUR = 0x01
UVCIOC_CTRL_QUERY = 0xC0107521

MODES = [
    (0, "关闭"),
    (1, "逐帧交替"),
    (2, "背景减除"),
    (3, "交替+减除"),
]


class XuQ(ctypes.Structure):
    _fields_ = [("unit", ctypes.c_uint8), ("selector", ctypes.c_uint8),
                ("query", ctypes.c_uint8), ("size", ctypes.c_uint16),
                ("data", ctypes.c_void_p)]


def xu(fd, query, size, buf):
    req = XuQ(unit=UNIT, selector=SELECTOR, query=query, size=size,
              data=ctypes.cast(buf, ctypes.c_void_p))
    fcntl.ioctl(fd, UVCIOC_CTRL_QUERY, req)


def gui_env():
    e = dict(os.environ)
    e["DBUS_SESSION_BUS_ADDRESS"] = BUS
    return e


def notify(title, body, ms=6000):
    try:
        subprocess.run(["notify-send", "-u", "critical", "-t", str(ms),
                        "-a", "红外补光灯", title, body],
                       env=gui_env(), capture_output=True, timeout=10)
    except Exception as e:      # 没有通知服务也不致命
        print(f"  （通知发送失败: {e}）")


def ask(title, body):
    """弹窗问操作者。返回 True=看到红点"""
    try:
        r = subprocess.run(["zenity", "--question", "--title", title,
                            "--text", body,
                            "--ok-label", "✅ 看到红点了",
                            "--cancel-label", "❌ 没看到",
                            "--width", "380"],
                           env=gui_env(), capture_output=True, timeout=300)
        return r.returncode == 0
    except FileNotFoundError:
        # 没有 zenity 就退化成终端问答
        return input("  看到红点了吗? [y/N] ").strip().lower() == "y"


def get_len_info_cur(fd):
    lb = (ctypes.c_uint8 * 2)()
    xu(fd, UVC_GET_LEN, 2, lb)
    n = lb[0] | (lb[1] << 8)

    ib = (ctypes.c_uint8 * 1)()
    xu(fd, UVC_GET_INFO, 1, ib)

    cb = (ctypes.c_uint8 * n)()
    xu(fd, UVC_GET_CUR, n, cb)
    return n, ib[0], bytes(cb)


def set_mode(fd, mode):
    n, info, _ = get_len_info_cur(fd)
    if not (info & 0x02):
        return None
    buf = (ctypes.c_uint8 * n)()
    xu(fd, UVC_GET_CUR, n, buf)
    buf[2] = mode
    xu(fd, UVC_SET_CUR, n, buf)
    time.sleep(0.3)
    _, _, back = get_len_info_cur(fd)
    return back


def hexs(b):
    return " ".join(f"{x:02x}" for x in b)


def main():
    try:
        import cv2
        import numpy as np
    except ImportError:
        sys.exit("缺少依赖：python3-opencv")

    print("=" * 58)
    print("  红外补光灯模式普查")
    print("=" * 58)
    print(f"  设备 {DEVICE}  单元 {UNIT}  selector 0x{SELECTOR:02x}")
    print(f"  存图目录 {OUTDIR}")
    print()
    print("  ⚠ 会向私有 UVC 扩展单元写入。请先确认你确实需要。")
    print()
    if input("  继续? [y/N] ").strip().lower() != "y":
        return 1

    cap = cv2.VideoCapture(DEVICE, cv2.CAP_V4L2)
    if not cap.isOpened():
        sys.exit(f"✘ 打不开 {DEVICE}")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
    for _ in range(15):
        cap.read()

    fd = os.open(DEVICE, os.O_RDWR)
    os.makedirs(OUTDIR, exist_ok=True)

    notify("红外补光灯测试开始",
           "接下来会依次切换 4 个模式。\n每个模式请看笔记本上的摄像头小孔。")

    results = {}
    try:
        for mode, name in MODES:
            print(f"\n【mode={mode}】（{name}）")
            back = set_mode(fd, mode)
            if back is None:
                print("  跳过（GET_INFO 说不支持写入）")
                continue
            print(f"  读回 {hexs(back)}  mode={back[2]}")

            notify(f"mode={mode}  {name}",
                   "现在请看向摄像头小孔，保持 5 秒…", ms=6000)

            frames, t0 = [], time.time()
            while time.time() - t0 < 5.0 and len(frames) < 120:
                ok, f = cap.read()
                if not ok:
                    continue
                if f.ndim == 3:
                    f = f[:, :, 0]
                frames.append(f)

            if frames:
                arr = np.stack(frames).astype(np.float32)
                mf = arr.mean(axis=0)
                alt = abs(float(arr[0::2].mean()) - float(arr[1::2].mean()))
                lo, hi = float(mf.min()), float(mf.max())
                vis = (mf - lo) / (hi - lo) * 255 if hi > lo else mf
                cv2.imwrite(f"{OUTDIR}/survey_mode{mode}.png",
                            np.clip(vis, 0, 255).astype(np.uint8))
                whole, fmax = float(arr.mean()), float(mf.max())
                print(f"  帧={len(frames)}  平均={whole:.2f}  "
                      f"最亮={fmax:.2f}  交替差={alt:.2f}")
            else:
                whole = fmax = alt = float("nan")
                print("  没采到帧")

            seen = ask(f"mode={mode} — {name}",
                       f"刚才（mode={mode}，{name}）时，\n"
                       f"摄像头小孔旁边的【红点】亮了吗？")
            results[mode] = dict(name=name, seen=seen, whole=whole,
                                 fmax=fmax, alt=alt)
            print(f"  → 操作者报告：{'✅ 看到了' if seen else '❌ 没看到'}")

        print("\n" + "=" * 58)
        print("  结论")
        print("=" * 58)
        print(f"  {'模式':<6}{'名称':<12}{'红点':<8}{'平均':>8}{'最亮':>8}{'交替差':>9}")
        for mode, r in sorted(results.items()):
            print(f"  {mode:<6}{r['name']:<12}"
                  f"{'✅ 亮' if r['seen'] else '—':<8}"
                  f"{r['whole']:>8.2f}{r['fmax']:>8.2f}{r['alt']:>9.2f}")

        lit = [m for m, r in results.items() if r["seen"]]
        print()
        print(f"  ✔ 点亮红点的模式: {lit}" if lit else "  ✘ 四个模式都没点亮")
        print("\n  ⚠ 提醒：各模式下的帧率可能不同，会影响与房间灯光的混叠。")
        print("    结论请用 ir-emitter-control.py 的 RGB 对照组复核。")
        return 0
    finally:
        os.close(fd)
        cap.release()


if __name__ == "__main__":
    sys.exit(main())
