#!/usr/bin/env python3
"""
开机/唤醒时打开 ThinkPad L14 Gen 1 集成摄像头的红外补光灯。

为什么需要它
------------
`/dev/video2` 的 MSXU FACE_AUTHENTICATION 控制项**上电默认值是 1，而 1 = 补光灯关闭**。
所以每次真正断电重启后，红外灯都是灭的，Iris 的人脸认证会以
`camera_error`（所有帧都低于 min_frame_brightness）失败。

实测：mode 1 = 灭；mode 0 / 2 / 3 = 亮（三者完全等效，亮帧占比正好 50%）。

安全原则（对比 linux-enable-ir-emitter 的暴力搜索）
-------------------------------------------------
  · 只碰微软文档化的那一个扩展单元和它自己声明的 selector
  · 用 GET_LEN 问出长度，用 GET_INFO 确认设备允许写入
  · 以设备自己上报的 GET_CUR 为模板，**只改第 3 个字节**
  · 目标值必须落在该控制项自己声明的 GET_MIN..GET_MAX 之内
  · 写完后读回校验
  · 不做任何盲试、不做枚举、不写其它 selector
"""
import ctypes
import fcntl
import os
import sys
import time

DEVICE = "/dev/video2"
UNIT = 14              # MSXU 在红外侧的单元号（USB 描述符里读出）
SELECTOR = 0x06        # FACE_AUTHENTICATION
MODE = 3               # 0/2/3 等效且会点亮；1 = 关闭
WAIT_SECONDS = 30      # 等待设备出现的最长时间

UVC_GET_CUR = 0x81
UVC_GET_MIN = 0x82
UVC_GET_MAX = 0x83
UVC_GET_LEN = 0x85
UVC_GET_INFO = 0x86
UVC_SET_CUR = 0x01
UVCIOC_CTRL_QUERY = 0xC0107521


class UvcXuControlQuery(ctypes.Structure):
    _fields_ = [
        ("unit", ctypes.c_uint8), ("selector", ctypes.c_uint8),
        ("query", ctypes.c_uint8), ("size", ctypes.c_uint16),
        ("data", ctypes.c_void_p),
    ]


def xu(fd, query, size, buf):
    req = UvcXuControlQuery(unit=UNIT, selector=SELECTOR, query=query,
                            size=size,
                            data=ctypes.cast(buf, ctypes.c_void_p))
    fcntl.ioctl(fd, UVCIOC_CTRL_QUERY, req)


def log(msg):
    print(f"ir-emitter-mode: {msg}", flush=True)


def wait_for_device():
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        if os.path.exists(DEVICE):
            try:
                fd = os.open(DEVICE, os.O_RDWR)
                return fd
            except OSError:
                pass
        time.sleep(1)
    return None


def main():
    fd = wait_for_device()
    if fd is None:
        log(f"{DEVICE} 在 {WAIT_SECONDS}s 内不可用；放弃（人脸仍会落回密码）")
        return 0                      # 不阻断启动

    try:
        # 问长度
        lb = (ctypes.c_uint8 * 2)()
        xu(fd, UVC_GET_LEN, 2, lb)
        length = lb[0] | (lb[1] << 8)
        if not 3 <= length <= 64:
            log(f"GET_LEN 异常 ({length})；不写入")
            return 0

        # 问是否允许写入
        ib = (ctypes.c_uint8 * 1)()
        xu(fd, UVC_GET_INFO, 1, ib)
        if not (ib[0] & 0x02):
            log("设备声明该控制项不支持写入；不写入")
            return 0

        # 取设备自己上报的上下限
        mn = (ctypes.c_uint8 * length)()
        mx = (ctypes.c_uint8 * length)()
        xu(fd, UVC_GET_MIN, length, mn)
        xu(fd, UVC_GET_MAX, length, mx)
        if not (bytes(mn)[2] <= MODE <= bytes(mx)[2]):
            log(f"目标值 {MODE} 超出设备自报范围 "
                f"[{bytes(mn)[2]}, {bytes(mx)[2]}]；不写入")
            return 0

        # 读当前值
        cur = (ctypes.c_uint8 * length)()
        xu(fd, UVC_GET_CUR, length, cur)
        before = bytes(cur)
        if before[2] == MODE:
            log(f"已经是 mode {MODE}（{before.hex(' ')}），无需改动")
            return 0

        # 以 GET_CUR 为模板，只改模式字节
        cur[2] = MODE
        xu(fd, UVC_SET_CUR, length, cur)
        time.sleep(0.3)

        back = (ctypes.c_uint8 * length)()
        xu(fd, UVC_GET_CUR, length, back)
        after = bytes(back)
        log(f"mode {before[2]} -> {after[2]}  （写 {bytes(cur).hex(' ')}）")
        if after[2] != MODE:
            log(f"读回是 {after[2]}，与目标 {MODE} 不符")
        else:
            log("红外补光灯已开启")
        return 0
    except OSError as e:
        log(f"ioctl 失败: {e}；放弃（人脸仍会落回密码）")
        return 0
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
