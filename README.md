# ThinkPad L14 Gen 1 红外人脸登录（Windows Hello 风格）

在 **Ubuntu 26.04 LTS** 上，用 [Iris](https://github.com/roryjmahoney/Iris) 给
**ThinkPad L14 Gen 1**（Chicony `04f2:b6be` 集成摄像头）配好红外人脸登录。

覆盖 **GDM 登录 / 锁屏解锁 / `sudo` 提权 / polkit 授权** 四个入口，
`iris doctor` **11 项检查全绿、0 警告、0 失败**。

人脸识别实测 **0.86 秒**通过，置信度 **0.80 ~ 0.98**（阈值 0.363）。

---

## 结论速览

配这个东西时，网上的教程和直觉会把你往**几个错误方向**带。以下是这台机器上的实测结论：

| 常见做法 / 直觉 | 实际情况 |
|---|---|
| 红外灯不亮 → 得用 `linux-enable-ir-emitter` 暴力搜索 UVC 命令 | ❌ **灯默认就会亮**。暴力搜索反而搅乱状态、长期占用设备 |
| 要往 MSXU 单元写 `SET_CUR` 才能点亮 | ❌ 不需要。默认值 `01 03 01 ...` 就是工作模式 |
| 环境亮就看不见红外灯 → 说明灯太弱 | ❌ 是**逐帧交替照明**，亮帧峰值远超环境光，所以环境亮也看得见 |
| 要写脚本 / systemd 服务做持久化 | ❌ 不需要，重启后天然可用 |
| 人脸登录配完就完事了 | ❌ 每次登录还会弹 **gnome-keyring** 解锁框，得单独处理（[发现 7](#发现-7人脸登录会顺带锁死-gnome-登录钥匙串)） |

**红外灯部分的元凶**：`linux-enable-ir-emitter` 服务会在**每次开机和唤醒**时往摄像头的
MSXU 扩展单元里写值，把设备推进异常状态。而且它有**把摄像头写砖**的公开前例
（见 [上游 issue #301](https://github.com/EmixamPP/linux-enable-ir-emitter/issues/301)）。

**结论：这个型号上，把它禁用掉，一切自然正常。**

---

## 环境

| 项目 | 值 |
|---|---|
| 机型 | LENOVO `20U6A1PFCD` — ThinkPad L14 Gen 1 |
| CPU / 内存 | AMD Ryzen 7 PRO 4750U (8C/16T) / 14 GB |
| 发行版 | Ubuntu 26.04 LTS (resolute) |
| 桌面 | GNOME 50.1 / **Wayland** |
| 内核 | 7.2.11（[自编译记录](https://github.com/Page0031/kernel-7.2.11-build-notes)） |
| 摄像头 | Chicony `04f2:b6be` Integrated Camera |
| Iris / Python / OpenCV | 0.2.1 / 3.14.4 / 4.10.0 |

---

## 摄像头硬件

一个 USB 设备，**5 个接口**，展开成 4 个 V4L2 节点：

| 节点 | 名称 | 类型 | 格式 |
|---|---|---|---|
| `/dev/video0` | Integrated Camera: Integrated **C** | 彩色 | MJPG 720p / YUYV 720p 等 |
| `/dev/video1` | 同上 | metadata | — |
| `/dev/video2` | Integrated Camera: Integrated **I** | **红外** | GREY 640x360 |
| `/dev/video3` | 同上 | metadata | — |

> `video0/1` 带**红外截止滤镜**（普通彩色相机），`video2/3` 是**红外**相机。
> Iris 的 `camera.device = "auto"` 会自动选中 `/dev/video2`。

```
$ iris cameras
     DEVICE       NAME                             TYPE      FORMATS
     /dev/video0  Integrated Camera: Integrated C  colour    MJPG, YUYV
  *  /dev/video2  Integrated Camera: Integrated I  infrared  GREY
```

### USB 描述符里的 MSXU

USB 描述符里能读到微软的 **Camera Control XU（MSXU）**：

| 接口 | 单元 ID | bmControls | 可用 selector |
|---|---|---|---|
| 0（彩色） | 8 | `0x01` | 只有 `0x09` METADATA |
| 2（**红外**） | **14** | `0x0122` | `0x02` EXPOSURE、**`0x06` FACE_AUTHENTICATION**、`0x09` METADATA |

红外侧那个单元 14 的 `0x06` 才是补光灯控制。**但这台机器上你不需要碰它**（详见下文）。

---

## 快速上手

```bash
# 1) 装 Iris（apt 优先，别用 pip）
cd Iris
sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' requirements-apt.txt \
  | xargs sudo apt install
sudo ./install.sh

# 2) 先体检，确认红外灯在工作
iris cameras
iris doctor

# 3) 录入人脸
sudo iris enroll

# 4) 验证识别
sudo iris test

# 5) 确认识别稳定后，再接入认证入口
sudo ./install.sh --all-pam    # GDM 登录+锁屏 / sudo / polkit

# 6) 关键：顺手修掉「人脸登录后 gnome-keyring 不解锁」的问题
#    不做这步的话，之后每次登录都会弹 Authentication required（详见「发现 7」）
sudo apt install tpm2-tools
sudo ./install.sh --keyring
sudo iris keyring enable
# 然后注销，用【密码】登录一次 GDM 以捕获钥匙串密码，再：
sudo iris keyring status       # 期望：ready
```

> ⚠️ 第 5/6 步会改 `/etc/pam.d`。Iris 的安装脚本自带**备份 + 结构校验 + 交互式密码验证
> + 失败自动回滚**，但最好还是**另外开一个终端保持登录**。
>
> ⚠️ 第 6 步有个坑：**只有「注销 + 用密码登录」才能捕获钥匙串密码**，
> 人脸登录和锁屏解锁都不行。细节见 [发现 7](#发现-7人脸登录会顺带锁死-gnome-登录钥匙串)。

---

## 核心发现

### 发现 1：补光灯默认就是好的，别去写 MSXU

这是最大的坑。表现是「`iris doctor` 报红外灯不亮」，于是很自然会去跑
`linux-enable-ir-emitter configure` 暴力搜索。

**实测：这台机器不跑任何东西，灯本来就是亮的。**

只读查询 MSXU 单元 14 / selector 0x06（`scripts/uvc-msxu.py`）得到的响应：

```
GET_LEN   长度 = 9 字节
GET_INFO  0x03   GET=支持  SET=支持
GET_CUR   01 03 01 00 00 00 00 00 00      mode=1
GET_MIN   00 00 00 00 00 00 00 00 00
GET_MAX   01 03 03 00 00 00 00 00 00      mode 上限 3
GET_DEF   01 03 01 00 00 00 00 00 00      mode=1
```

**`GET_CUR` 就等于 `GET_DEF`** —— 设备开箱即处于它的默认工作模式。
`GET_INFO` 说支持写入，但**支持写入 ≠ 必须写入**。

字段布局解读（`scripts/uvc-msxu.py` 会打印）：

```
字节0 版本      = 1
字节1 能力掩码  = 0x03  (bit0=逐帧交替照明, bit1=背景减除)
字节2 当前模式  = 1
```

### 发现 2：「闪烁」是逐帧交替照明

在 Windows 下会看到摄像头旁边有个**红点在闪烁**，而且**环境很亮也看得见**。
这曾经让我误判成「灯很弱、被环境光淹没了」。

实际情况：**补光灯是逐帧交替工作的 —— 奇数帧亮、偶数帧灭。**

- 亮帧的峰值亮度**远超**环境光，所以环境再亮也看得见那个一闪一闪的红点
- 灭帧用来做参考，摄像头内部做差分，这就是 Windows Hello 的抗环境光原理

Iris 抓住的正是这个模式：

```
✓ IR strobe   emitter strobing: 17 lit (mean 42.4) / 18 dark (mean 16.0), threshold 20.0
```

`iris hardware-report` 打出的原始模式串更直观（`L`=亮 `D`=灭）：

```
DLDLDLDLDLDLDLDLDLDLDLDL
```

### 发现 3：怎么证明「闪烁来自红外灯」而不是「房间灯光」

这是个方法论问题 —— 房间里的 LED / 日光灯本身就在 100 Hz 闪烁，
低帧率采样时会**混叠成逐帧交替**，能在红外相机上伪装成补光灯。

**判据：拿 RGB 相机做对照组。**

`/dev/video0` 有**红外截止滤镜** —— 它看得见房间灯光的可见光闪烁，
**看不见**红外补光灯。所以：

| RGB 相机 | 红外相机 | 结论 |
|---|---|---|
| 也交替 | 交替 | 是**房间灯光** → 假阳性 |
| **稳定** | 交替 | 是**红外补光灯** ✔ |

这台机器上的实测（`scripts/ir-emitter-control.py`）：

```
红外相机 video2   15.0 fps | 奇数帧= 43.32  偶数帧= 16.04  交替差= 27.27
RGB 相机 video0   30.0 fps | 奇数帧=103.97  偶数帧=104.01  交替差=  0.04
```

**RGB 侧 `0.04`（纹丝不动），红外侧 `27.27`（2.7 倍差异）** —— 决定性证据。

> 💡 这个方法对任何「低帧率相机测闪烁」的场景都适用。

### 发现 4：`linux-enable-ir-emitter` 是元凶，且有写砖风险

它注册了一个 systemd 服务，挂在这些 target 的 `Wants=` 上：

```ini
[Install]
WantedBy = multi-user.target suspend.target sleep.target
           hybrid-sleep.target hibernate.target suspend-then-hibernate.target
```

也就是说**每次开机、每次从睡眠唤醒**，它都会去写摄像头的 UVC 扩展单元。

**风险**：补光灯控制这类私有 XU 单元没有公开规范，盲写可能导致摄像头模块永久失效。
上游 issue #301 里有真实案例：

- SunplusIT `174f:11b4`（ThinkPad P14s Gen 6）—— 作者说笔记本**送修了**
- SPCA2087（ThinkPad T480）

而这些机器上**红外和彩色摄像头共用同一个 USB 设备**，
红外模块被写坏 = **连普通摄像头也一起废掉**。

**处置**：禁用服务，配置文件挪走备份（**不删**）：

```bash
sudo systemctl disable --now linux-enable-ir-emitter
sudo mv /etc/linux-enable-ir-emitter/*.ini \
        /etc/linux-enable-ir-emitter/<同名>.disabled-by-iris
```

### 发现 5：`/dev/video*` 上有 uaccess ACL —— 脚本不必用 root

```
$ getfacl -p /dev/video2
# file: /dev/video2
# owner: root
# group: video
user::rw-
user:<你的用户名>:rw-        ← 关键
group::rw-
mask::rw-
other::---
```

`systemd-logind` 会给**当前本机会话的登录用户**授予 `uaccess` ACL。
所以**普通用户就能直接读写红外摄像头和它的设备控制**，不需要 root、不需要加 `video` 组。

这让排查脚本可以跑在用户会话里，从而能直接用
`notify-send` / `zenity` 弹屏幕提示和问询窗口，全程自动化：

```bash
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus
notify-send -u critical "红外测试" "现在请看摄像头"
```

### 发现 6：`iris enroll` 的用户取自 `SUDO_USER`

`iris/cli/common.py`：

```python
name = explicit or os.environ.get("SUDO_USER") or ""
if not name:
    name = getpass.getuser()
```

所以：

| 命令 | 录到哪个账号 |
|---|---|
| `sudo iris enroll` | `$SUDO_USER` → **你自己** ✔ |
| `pkexec iris enroll` | 没有 `SUDO_USER` → `getpass.getuser()` → **root** |

用 `pkexec` 跑就会莫名其妙录到 root 名下。**显式指定**最稳：

```bash
sudo iris enroll --user <你的用户名>
```

**这台机器上两份都录了**，因为两个用途的 `PAM_USER` 不同：

| 账号 | 用途 | `PAM_USER` |
|---|---|---|
| 你的账号 | GDM 登录、锁屏解锁 | 你的账号 |
| `root` | `sudo`、polkit | `root` |

### 发现 7：人脸登录会「顺带锁死」GNOME 登录钥匙串

**这是整个流程里最容易卡住、也最少被文档提及的一步。**

配好人脸登录、重启之后，每次进桌面都会弹这个框：

> **Authentication required**
> The login keyring did not get unlocked when you logged into your computer.

#### 为什么会这样

`gnome-keyring` 的登录钥匙串**不是独立密码**，它的加密密钥是从**你的登录密码**派生出来的。
负责把密码交给它的是 `pam_gnome_keyring.so`：

```
auth  [success=done default=ignore]  pam_iris.so            ← 人脸，短路点
@include common-auth                                        ← 密码
auth  optional  pam_gnome_keyring.so                        ← 解开钥匙串 ← 被跳过
```

`success=done` 的语义是「**认证到此为止，后面的模块全部不执行**」。
所以人脸一通过，PAM 立刻返回成功，**`pam_gnome_keyring.so` 根本轮不到运行** ——
钥匙串拿不到密码，就没被解。

> 💡 `Windows Hello` 没有这个问题，是因为它走的是 DPAPI + TPM 的另一套体系，
> 钥匙串密钥不依赖登录密码。**任何「不经过密码的生物识别登录」在 Linux 上都会撞到这一堵墙。**

#### 解法：用 TPM 把密码封存起来

Iris 专门做了这个功能 —— 把登录密码用 TPM 2.0 封存，人脸登录时再解出来交给 Keyring：

```bash
sudo apt install tpm2-tools          # 额外依赖
sudo ./install.sh --keyring          # 给 gdm-password 加两行 optional 钩子
sudo iris keyring enable             # 按用户开启 -> 状态 pending-password-login
```

装完 `gdm-password` 会变成：

```
auth     [success=done default=ignore]  pam_iris.so
@include common-auth
auth     optional  pam_iris_keyring.so        ← 新增：捕获密码
auth     optional  pam_gnome_keyring.so
...
session  optional  pam_iris_keyring.so        ← 新增：会话打开时保存/释放
session  optional  pam_gnome_keyring.so auto_start
```

两行都是 **`optional`** —— 它出问题也**不可能挡住登录**，最坏就是退回弹框。

#### ⚠️ 关键：必须是「非人脸的真实登录」才能捕获密码

读一下 `pam/pam_iris_keyring.c` 就明白为什么：

```c
int pam_sm_authenticate(...)     // auth 阶段：把 PAM_AUTHTOK 暂存进 PAM data
int pam_sm_open_session(...)     // ★ 只有【会话打开】时才真正落库
{
    if (face)          → helper("unlock")   // 释放已存密码给钥匙串
    else if (captured) → helper("capture")  // 保存这次输入的密码
}
```

由此推出三条**反直觉的结论**：

| 你可能以为 | 实际 |
|---|---|
| 人脸登录会顺便把密码存下来 | ❌ 人脸走的是 `unlock` 分支，**永远不保存** |
| 锁屏解锁时输密码就行 | ❌ 锁屏**不触发 `open_session`**，暂存的密码会被丢弃 |
| 用 `sudo` 输密码也行 | ❌ `service` 字段硬编码为 `gdm-password`，其它 PAM 服务不参与 |

**只有「注销 + 用密码重新登录 GDM」这一条路。** 而且保存前会校验密码是否匹配账号当前哈希。

#### 正确操作顺序

```bash
# ① 先临时关掉人脸，逼出确定性的密码登录
sudo iris config set auth.enabled false

# ② 注销 -> 在 GDM 输入密码登录（会直接要密码，不会扫脸）

# ③ 确认已经落库
sudo iris keyring status          # pending-password-login -> ready
sudo ls -la /var/lib/iris/keyring/    # 空标记 125 字节 -> 密文 ~204 字节

# ④ ⚠ 步：把人脸开回来
sudo iris config set auth.enabled true

# ⑤ 再注销一次，这次用人脸登录，确认不再弹框
```

> 第 ④ 步很容易忘 —— `auth.enabled false` 是**全局**关闭，
> 挂在那里的时候 `sudo`、锁屏、登录全都只能输密码。

#### 一个无法用代码绕过的限制

Iris 保存的是**你登录时输入的那个密码**，并且只在「它匹配账号当前密码哈希」时才存。
它**无法验证**钥匙串自身的密码是否和登录密码一致。文档原话：

> The helper cannot verify that a separately configured keyring password matches your
> login password. If they differ, the standard keyring prompt remains.

如果你以前改过账号密码，钥匙串可能还是旧密码建的 —— 那这个方案解决不了，
得先用 Seahorse 把钥匙串密码改成登录密码（会需要重新保存已存的密钥）。

#### 安全边界（值得知道）

- 密码用 TPM 2.0 封存（`/var/lib/iris/keyring/`，目录 `0700`、文件 `0600`），
  密钥由 Iris 已有的 TPM 主密钥派生，绑定用户名 + UID + 当前密码哈希
- 明文密码只在内存和私有管道里，**不落盘、不进命令行/环境变量、不写日志**
- **但**：这是**设备绑定存储，不是硬件强制的活体检测**。
  root 可以解封，攻击者控制的系统也能解封；成功的照片/面具欺骗可能连带泄露钥匙串
- **保存的就是你的账号密码本身**，接受不了就别开这个功能

---

## 排查过程：模式普查

在确认「默认模式就行」之前，做过一次完整的模式普查。
用屏幕通知 + 问询窗口，让操作者自己报告每个模式有没有看到红点
（`scripts/ir-mode-survey.py`）：

| 模式 | 名称 | 看到红点？ | 画面平均亮度 | 逐帧交替差 |
|---|---|---|---|---|
| 0 | 关闭 | ✅ | 28.98 | 25.59 |
| **1** | 逐帧交替 | **❌** | **17.66** | **0.93** |
| 2 | 背景减除 | ✅ | 31.98 | 31.43 |
| 3 | 交替+减除 | ✅ | 30.23 | 28.81 |

模式 1 的画面又暗（17.66）、交替差又几乎为 0（0.93），**眼睛和数据完全一致**。

> ⚠️ 但这个普查有个已知的干扰因素：相机在每个模式下的**帧率不同**
> （模式 1 跑 24 fps，其它跑 15.8 fps），采样率变化会改变与房间灯光混叠的结果。
> 而**后面用 RGB 对照组 + 默认模式跑 `iris doctor` 得到的结论更可靠**：
> 默认状态下补光灯就是正常交替工作的。

**最终没有采用任何 `SET_CUR` 方案** —— 既然默认模式就能过，就不去动私有寄存器。

---

## 验证

### `iris doctor` 全绿

```
Iris 0.2.1 — system health check

  ✓  Python runtime      Python 3.14, numpy 2.3.5, OpenCV 4.10.0, cryptography 46.0.5
  ✓  Models              YuNet + SFace present in /usr/share/iris/models (38017 KiB)
  ✓  Configuration       /etc/iris/config.toml is valid (0644 root:root)
  ✓  Template store      /var/lib/iris is root:root 0700
  ✓  Daemon              running and answering on /run/irisd/socket (version 0.2.1)
  ✓  Socket permissions  /run/irisd/socket is root:root 0600
  ✓  PAM module          pam_iris.so, referenced by gdm-password, polkit-1, sudo
  ✓  IR camera           auto → /dev/video2: 35 frames in 2.6s (13.5 fps), 640x360
  ✓  IR strobe           emitter strobing: 17 lit (mean 40.4) / 18 dark (mean 16.0), threshold 20.0
  ✓  Enrollment          1 face(s) for root: default (15 samples)
  ✓  TPM                 available; the master key is sealed to it (/dev/tpmrm0)

11 checks: 11 passed, 0 warnings, 0 failed
```

### 识别测试

```
$ sudo iris test --user <你的用户名>
✓ Authenticated as default in 0.89s
  confidence 0.835  (threshold 0.363, margin +0.472, 3 consecutive matches required)

$ sudo iris test --user root
✓ Authenticated as default in 0.86s
  confidence 0.804  (threshold 0.363, margin +0.441, 3 consecutive matches required)
```

### 四个认证入口

| 场景 | PAM 服务 | 怎么试 |
|---|---|---|
| GDM 登录 | `gdm-password` | 注销后选账号露脸 |
| 锁屏解锁 | `gdm-password` | `Super+L` 后露脸 |
| 终端提权 | `sudo` | `sudo -k; sudo ls` |
| 图形授权 | `polkit-1` | 触发任意 polkit 弹窗 |

三个文件的控制字段都是 fail-through —— **人脸失败自动落回密码**：

```
auth [success=done default=ignore] pam_iris.so
```

PAM 备份在 `/var/backups/iris/`。安装脚本还会专门验证模块**不会把登录卡死**：

```
✓ module loads, returns in 2s, cannot hang
```

---

## 目录内容

```
README.md                       本文
LICENSE                         MIT
scripts/
  ir-emitter-control.py         ⭐ RGB/IR 对照组实验 —— 判断闪烁来源（只用标准库 + cv2）
  uvc-msxu.py                   MSXU 扩展单元只读查询 / luma 采样（默认只读，绝对安全）
  ir-mode-survey.py             带屏幕通知与问询窗口的模式普查
config/
  iris-config.toml              实测可用的 Iris 配置
logs/
  iris-doctor.txt               11 项全绿的体检输出
  hardware-report.md            iris hardware-report 原始输出
  video-device-acl.txt          /dev/video2 的权限与 ACL
  keyring-status.txt            钥匙串自动解锁的核对证据 + 回滚方法
```

脚本用法：

```bash
# 判断红外灯是否真的在闪（不需要 root，靠 uaccess ACL）
python3 scripts/ir-emitter-control.py

# 只读查询 MSXU 单元（不写任何东西）
sudo python3 scripts/uvc-msxu.py query
sudo python3 scripts/uvc-msxu.py luma          # 采样红外画面平均亮度
```

---

## 常见坑速查

| 症状 | 原因 | 处理 |
|---|---|---|
| `iris doctor` 说红外灯不亮 | `linux-enable-ir-emitter` 搅乱状态 / 占用设备 | 禁用它，重启后再体检 |
| 灯看起来「一点都不亮」 | 它在逐帧交替，人眼容易漏掉那一瞬 | 用 `scripts/ir-emitter-control.py` 客观测 |
| `sudo iris enroll` 录错账号 | `SUDO_USER` 缺失（`pkexec` 下） | 显式 `--user <用户名>` |
| 终端输密码反复认证失败 | 中文输入法截走字符组字 | 输密码前 `Super+空格` 切英文 |
| 戴眼镜识别不稳 | 只录了不戴眼镜的样本 | 再录一份：`sudo iris enroll glasses` |
| **每次登录都弹 `Authentication required`** | `pam_iris.so` 的 `success=done` 短路了 PAM，`pam_gnome_keyring.so` 没跑 | `install.sh --keyring` + `iris keyring enable`，**再用密码登录一次**（见发现 7） |
| `iris keyring status` 一直是 `pending` | 用的人脸登录或锁屏解锁 —— 两者都不触发会话打开 | 关掉人脸 → **注销** → 用密码登录 |
| 关掉人脸后忘了开回来 | `auth.enabled false` 是全局的 | `sudo iris config set auth.enabled true` |

---

## 参考

- [Iris](https://github.com/roryjmahoney/Iris) —— 本项目用的实现
- [linux-enable-ir-emitter issue #301](https://github.com/EmixamPP/linux-enable-ir-emitter/issues/301)
  —— 补光灯激活讨论 + 写砖案例
- [Microsoft Camera Control XU（MSXU）](https://learn.microsoft.com/en-us/windows-hardware/drivers/stream/uvc-extensions-1-5)
- [USB Video Class 1.5 规范](https://www.usb.org/document-library/video-class-v15-document-set)

---

## 许可

MIT，见 [LICENSE](LICENSE)。
本仓库记录的**是这台具体机器上的实测结果**；其他型号请自行验证，尤其是涉及
私有 UVC 扩展单元的写入操作。
