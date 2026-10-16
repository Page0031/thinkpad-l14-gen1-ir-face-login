#!/usr/bin/env python3
"""
只读查询微软 Camera Control XU（MSXU）的 FACE_AUTHENTICATION 控制项，
用于确认红外补光灯当前处于什么模式。**默认完全不写入。**

背景
----
很多教程说「红外灯不亮就要往 MSXU 写 SET_CUR」。**在这台机器上不需要** ——
设备的 GET_CUR 就等于 GET_DEF，开箱即处于正确模式。本脚本先帮你确认这一点。

安全原则（来自 linux-enable-ir-emitter issue #301 的教训）
----
  · 只碰微软文档化的那个扩展单元
  · 只碰该单元自己声明的 selector
  · 先全部用 GET_* 只读读取
  · 只有 GET_INFO 明确说支持写入时，`set` 子命令才动手
  · 只写 GET_MIN..GET_MAX 范围内的值

  这类私有 XU 没有公开规范，盲写有**把摄像头模块写砖**的公开前例
  （同型号机器上红外与彩色共用一个 USB 设备，写坏红外 = 彩色摄像头一起废）。

子命令
----
    query           只读，打印 GET_LEN / GET_INFO / CUR / MIN / MAX / DEF
    luma [秒数]     采样红外画面的平均/最高/最低亮度
    set <mode>      ⚠ 写入模式（默认不提供；确认需要后再用）

用法
----
    sudo python3 uvc-msxu.py query
    sudo python3 uvc-msxu.py set 1 --device /dev/video2 --unit 14

注意：读 /dev/video* 通常不需要 root（systemd-logind 的 uaccess ACL 会授权给
      本机登录用户）。但如果走了 sudo 之外的提权，ACL 未必生效，那时才需要 root。
"""
import ctypes
import fcntl
import os
import sys
import time

DEVICE = "/dev/video2"      # 红外节点
UNIT = 14                   # MSXU 在红外侧的单元号（从 USB 描述符读出）
SELECTOR = 0x06             # FACE_AUTHENTICATION

UVC_GET_CUR = 0x81
UVC_GET_MIN = 0x82
UVC_GET_MAX = 0x83
UVC_GET_RES = 0x84
UVC_GET_LEN = 0x85
UVC_GET_INFO = 0x86
UVC_GET_DEF = 0x87
UVC_SET_CUR = 0x01

UVCIOC_CTRL_QUERY = 0xC0107521


class UvcXuControlQuery(ctypes.Structure):
    """struct uvc_xu_control_query（16 字节）"""
    _fields_ = [
        ("unit", ctypes.c_uint8),
        ("selector", ctypes.c_uint8),
        ("query", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("data", ctypes.c_void_p),
    ]


def xu_query(fd, unit, selector, query, size, buf):
    req = UvcXuControlQuery(
        unit=unit, selector=selector, query=query, size=size,
        data=ctypes.cast(buf, ctypes.c_void_p),
    )
    fcntl.ioctl(fd, UVCIOC_CTRL_QUERY, req)


def hexs(b):
    return " ".join(f"{x:02x}" for x in b)


def open_len_info(fd):
    """返回 (payload 长度, GET_INFO 位掩码)"""
    lb = (ctypes.c_uint8 * 2)()
    xu_query(fd, UNIT, SELECTOR, UVC_GET_LEN, 2, lb)
    length = lb[0] | (lb[1] << 8)

    ib = (ctypes.c_uint8 * 1)()
    xu_query(fd, UNIT, SELECTOR, UVC_GET_INFO, 1, ib)
    return length, ib[0]


def do_query():
    if not os.path.exists(DEVICE):
        print(f"✘ 设备不存在: {DEVICE}")
        return 1

    fd = os.open(DEVICE, os.O_RDWR)
    try:
        print(f"设备:       {DEVICE}")
        print(f"MSXU 单元:  {UNIT}")
        print(f"selector:   0x{SELECTOR:02x} (FACE_AUTHENTICATION)")
        print()

        try:
            length, info = open_len_info(fd)
        except OSError as e:
            print(f"✘ GET_LEN 失败: {e}")
            print("  → 该控制项不存在或不被支持（没有写入任何东西）")
            return 1

        print(f"  GET_LEN   长度 = {length} 字节")
        if not (0 < length <= 64):
            print(f"✘ 长度异常 ({length})，中止")
            return 1

        print(f"  GET_INFO  0x{info:02x}   "
              f"GET={'支持' if info & 0x01 else '不支持'}  "
              f"SET={'支持' if info & 0x02 else '不支持'}")
        print()

        cur = None
        for name, code in (("GET_CUR", UVC_GET_CUR), ("GET_MIN", UVC_GET_MIN),
                           ("GET_MAX", UVC_GET_MAX), ("GET_DEF", UVC_GET_DEF)):
            buf = (ctypes.c_uint8 * length)()
            try:
                xu_query(fd, UNIT, SELECTOR, code, length, buf)
                raw = bytes(buf)
                if name == "GET_CUR":
                    cur = raw
                print(f"  {name}   {hexs(raw)}"
                      + (f"      mode={raw[2]}" if length > 2 else ""))
            except OSError as e:
                print(f"  {name}   失败: {e}")

        if cur and len(cur) >= 3:
            print()
            print("  解读:")
            print(f"    字节0  版本      = {cur[0]}")
            print(f"    字节1  能力掩码  = 0x{cur[1]:02x} "
                  f"(bit0={'逐帧交替照明' if cur[1] & 1 else '无'}, "
                  f"bit1={'背景减除' if cur[1] & 2 else '无'})")
            print(f"    字节2  当前模式  = {cur[2]}")
            print()
            print("  ⚠ 若 GET_CUR 与 GET_DEF 相同，说明设备已处于默认工作模式，")
            print("    通常【不需要】写入任何东西。")
        return 0
    finally:
        os.close(fd)


def do_set(mode):
    fd = os.open(DEVICE, os.O_RDWR)
    try:
        length, info = open_len_info(fd)
        if not (info & 0x02):
            print("✘ GET_INFO 说该控制项不支持写入，拒绝操作")
            return 1

        buf = (ctypes.c_uint8 * length)()
        xu_query(fd, UNIT, SELECTOR, UVC_GET_CUR, length, buf)
        before = bytes(buf)
        print(f"  SET 前 GET_CUR: {hexs(before)}"
              f"  (mode={before[2] if length > 2 else '-'})")

        buf[2] = mode
        print(f"  准备写入:       {hexs(bytes(buf))}  (mode={mode})")

        xu_query(fd, UNIT, SELECTOR, UVC_SET_CUR, length, buf)
        print("  ✔ SET_CUR 已发送")

        time.sleep(0.5)
        after = (ctypes.c_uint8 * length)()
        xu_query(fd, UNIT, SELECTOR, UVC_GET_CUR, length, after)
        print(f"  SET 后 GET_CUR: {hexs(bytes(after))}")
        return 0
    finally:
        os.close(fd)


def do_luma(seconds=3.0):
    """采样红外画面亮度。只看整幅平均值会严重稀释小光斑，故同时给出最大值。"""
    try:
        import cv2
    except ImportError:
        print("  ✘ 没有 cv2，无法采样（python3-opencv）")
        return 1

    cap = cv2.VideoCapture(DEVICE, cv2.CAP_V4L2)
    if not cap.isOpened():
        print(f"  ✘ 打不开 {DEVICE}（被占用？）")
        return 1

    vals, t0 = [], time.time()
    while time.time() - t0 < seconds:
        ok, frame = cap.read()
        if not ok:
            continue
        vals.append(float(frame.mean()))
    cap.release()

    if not vals:
        print("  ✘ 没采到帧")
        return 1

    print(f"  {DEVICE}  帧数 {len(vals)}  "
          f"平均 {sum(vals)/len(vals):.2f}  "
          f"最高 {max(vals):.2f}  最低 {min(vals):.2f}")
    print("  ⚠ 补光灯是小光斑，整幅平均值会被稀释；请配合 ir-emitter-control.py 判断")
    return 0


def main():
    argv = sys.argv[1:]
    global DEVICE, UNIT, SELECTOR

    # 支持 --device / --unit / --selector 覆盖
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--device" and i + 1 < len(argv):
            DEVICE = argv[i + 1]; i += 2
        elif argv[i] == "--unit" and i + 1 < len(argv):
            UNIT = int(argv[i + 1]); i += 2
        elif argv[i] == "--selector" and i + 1 < len(argv):
            SELECTOR = int(argv[i + 1], 0); i += 2
        else:
            rest.append(argv[i]); i += 1

    cmd = rest[0] if rest else "query"

    if cmd == "query":
        return do_query()
    if cmd == "set":
        if len(rest) < 2:
            print("用法: uvc-msxu.py set <mode> [--device ... --unit ...]")
            return 1
        print("⚠ 写入私有 XU 有风险，请确认你确实需要这么做。")
        return do_set(int(rest[1]))
    if cmd == "luma":
        return do_luma(float(rest[1]) if len(rest) > 1 else 3.0)

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
