<<<<<<< HEAD
# QQ + DeepSeek 聊天机器人

一个跑在自己电脑/服务器上的 QQ 机器人：**QQ 侧用 NapCat（OneBot v11 协议）**，
**大脑用 DeepSeek API**。Python 编写，单进程，依赖只有 `websockets` + `httpx`。

```
QQ 客户端 ──▶ NapCat（协议端） ──正向 WebSocket──▶ 本机器人 ──HTTPS──▶ DeepSeek API
```

## 功能一览

| 需求 | 实现 | 对应配置 |
| --- | --- | --- |
| 1. 设定人格 | `config/persona.md` 写人设，**保存即热加载**；还能给单个群开小灶 | `PERSONA_FILE` / `PERSONA_DIR` |
| 2. 群里只有 @ 才回应 | 检测 `at` 消息段且 qq == 机器人自身 QQ，其余消息完全静默 | `GROUP_AT_ONLY` |
| 3. 机器人被拉进新群时打招呼 | 监听 `group_increase`（user_id == 自己）发欢迎语 | `GROUP_JOIN_GREETING` |
| 4. 群友加群时欢迎 | 监听 `group_increase`，自动查昵称并 `@` 新人 | `WELCOME_TEMPLATE` |

按时打招呼（新增）：

| 行为 | 触发时机 | 对应配置 |
| --- | --- | --- |
| 戳一戳回应 | 有人戳机器人（`notice_type=notify` + `sub_type=poke`，且被戳的是自己） | `POKE_REPLY_ENABLED` / `POKE_BACK` |
| 表情包回应 | 被 @ 或私聊收到表情包（`mface` / 带 summary 的图片） | `STICKER_REPLY_ENABLED` / `STICKER_RANDOM_CHANCE` |
| 晚安播报 | 每天到达设定时间，向所有群发一句 | `GOODNIGHT_TIME` / `GOODNIGHT_TEXT` / `GOODNIGHT_JITTER` |
| 每日新闻 | 每天 12:00 发一条国内外要闻摘要（国外 5 条 + 国内 5 条，每条约 20 字） | `NEWS_TIME` / `NEWS_FOREIGN` / `NEWS_DOMESTIC` / `NEWS_ITEM_CHARS` |
| 群聊插话 | 除被 @ 外，每半小时自己读 5 条群聊（带时间）接一句 | `AMBIENT_ENABLED` / `AMBIENT_INTERVAL` / `AMBIENT_MESSAGES` / `AMBIENT_GROUP_WHITELIST` |
| 启动问候 | 每次进程启动后（断线重连不重复） | `STARTUP_GREETING` / `STARTUP_GREETING_DELAY` |

音游查分（集成了提比 Tippy 的查分能力）：

| 指令 | 作用 |
| --- | --- |
| `/search 关键词` | 查歌（歌名 / ID，去标点模糊匹配） |
| `/id ID` | 歌曲详情（曲师、BPM、各难度定数） |
| `/score 歌名或ID` | 我的舞萌单曲成绩 |
| `/b50` `/b40` `/ap50` | 舞萌 Best 50 / Best 40 / AP 50 |
| `/cscore 歌名或ID` | 我的中二单曲成绩 |
| `/chub30` | 中二 Best 30 |
| `/定数表 13+` `/中二定数表 14+` | 定数表 |
| `/jrys` | 今日运势 |
| `/roll a b` | 帮你选一个 |
| `#查询日本时间` | 世界各地时间 |
| `/音游帮助` | 指令列表 |

额外能力（都已实现）：

- **多轮上下文记忆**：按会话隔离，自动按轮数 / 过期时间裁剪；支持「全群共享上下文」或「每人独立上下文」
- **私聊直聊**：私聊不用 @，直接对话
- **限流 + 群黑白名单**：每人每窗口次数、全局次数、按群开关
- **消息去重 / 撤回同步遗忘**：重复事件不重复回复，撤回的消息会从记忆里删掉
- **优雅降级**：DeepSeek 报错 / 超时不会让机器人崩，会回一句人话
- **长回复自动分段**：按段落 / 句号切分，不会因为超出 QQ 字数上限发不出去
- **Markdown 清洗**：QQ 不渲染 Markdown，自动去掉 `**`、反引号、标题符号
- **图片 / 语音转描述**：模型看不见的内容会变成「（发了一张图片）」，让模型知道发生了什么
- **内置指令**：`重置对话`、`帮助`、`状态`
- **日志滚动**：控制台 + `logs/bot.log`（5MB × 3）
- **环境自检**：`python run.py --check` 一次性验证配置 / DeepSeek / 协议端

---

## 快速开始

### 第 1 步：准备 NapCat（QQ 协议端）

NapCat 是目前维护最活跃的 NTQQ 协议端，扫码登录即可。

> ⚠️ **先读这段，能省你半小时：NapCat 自带的 QQ 下载链接基本都是失效的。**
>
> 现象是安装器报 `HTTP状态码: 404` / `下载QQ失败`。
> 原因：NapCat 把推荐 QQ 版本的下载地址**硬编码**在程序里（Release 说明里那条 `dldir1.qq.com/...QQ9.9.x.exe`，
> 以及一键包 `NapCatInstaller.exe` 内部写死的 `.../be71d851/QQ9.9.26.44498_x64.exe`）。
> 腾讯会定期轮换这些带哈希的路径，**旧链接必死**，跟你网络没关系。
>
> 👉 **结论：不要用一键包。自己装 QQ，再配 NapCat.Shell —— 这样完全不依赖 NapCat 的下载链接。**

#### 方式 A：自己装 QQ + NapCat.Shell（✅ 推荐，最稳）

1. **去 QQ 官网下载最新版 QQ NT**（浏览器里正常点「下载」即可，这个链接腾讯一直维护）：
   <https://im.qq.com/pcqq/index.shtml>
   装好并登录一次，确认 QQ 本身能正常用。构建号 **≥ 40768** 即可（NapCat 的最低要求，最新版肯定满足）。

2. **下载 `NapCat.Shell.zip`**（约 29 MB，不含 QQ，所以不受上面的问题影响）：
   打开 <https://github.com/NapNeko/NapCatQQ/releases> 找最新版的 `NapCat.Shell.zip`
   直链：<https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip>

3. **解压后双击 `launcher.bat`**（Win10 用 `launcher-win10.bat`）。
   想跳过扫码可直接把 QQ 号当参数传进去：`launcher.bat 123456`

#### 方式 B：可视化管理工具

单文件 EXE，带安装引导，能自动检测环境、一键启停、多账号管理：

<https://github.com/NapNeko/NapCatQQ-Desktop/releases>

#### 方式 C：一键版（不推荐，链接经常失效）

```
https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.Windows.OneKey.zip
```

解压 → 双击 `NapCatInstaller.exe` → 进入生成的 `NapCat.XXXXX.Shell` 目录 → 双击 `napcat.bat`。

这个包只有 1 MB，因为 QQ 是安装时现下载的；而那个地址就写死在 `NapCatInstaller.exe` 里，
一旦腾讯轮换路径就会像你遇到的那样 404。**只有在方式 A 不方便时才试它。**

> 💡 偏方（未经验证）：安装器第一步会「检查QQ安装包」，说明它会先找本地文件。
> 可以把自己从官网下到的 QQ 安装器放到 `NapCatInstaller.exe` 同目录并改名试试，
> 但不如直接走方式 A 省事。

#### 下载慢的话用镜像

GitHub 在部分网络下时通时断。把原始地址接在镜像后面即可：

```text
https://gh-proxy.net/https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip
https://ghfile.geekertao.top/https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip
https://github.tbedu.top/https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip
```

> 旧教程里常见的 `github.moeyy.xyz` 目前已经不可用，别再用了。

#### 备选：Docker 跑 NapCat（完全不用装 QQ）

`mlikiowa/napcat-docker` 镜像里已经内置 QQ，配合本项目的 `docker-compose.yml` 一条命令起，
适合已经装了 Docker Desktop 的情况，详见文末「Docker 一键部署」。

#### 配置 WebSocket 服务

NapCat 启动、扫码登录后，进入 **网络配置**，新增一个 **WebSocket 服务器**（正向 WS）：

| 配置项 | 建议值 |
| --- | --- |
| 名称 | deepseek-bot |
| Host | `127.0.0.1` |
| Port | `3001` |
| Token | 留空，或自己设一个（要和 `.env` 里一致） |
| 消息格式 | `array`（推荐） |

> 也可以在 Docker 里跑 NapCat，见下方「Docker 一键部署」。

### 第 2 步：配置机器人

```powershell
cd qq-deepseek-bot
Copy-Item .env.example .env
notepad .env      # 填入 DEEPSEEK_API_KEY
```

DeepSeek API Key 在 <https://platform.deepseek.com/api_keys> 申请，新账号通常有赠送额度。

### 第 3 步：启动（一个脚本全搞定）

本项目已经把 `NapCat.Shell` 放在根目录下，并配好了 OneBot 的 WebSocket 服务。
**双击 `start.bat` 就行**——它会依次启动 NapCat、等你登录、再把机器人拉起来。

```
双击 start.bat                    二维码登录；登录成功后自动转后台，窗口自己关掉
双击 start.bat -QQ 3104685327     免扫码快速登录，全程没有任何窗口
双击 start.bat -Foreground        前台运行，方便盯着日志排错
双击 start.bat -Status            只看当前跑没跑
双击 start.bat -TestQr            测试二维码图片能不能正常弹出
双击 stop.bat                     一键全部停掉
```

### 二维码：认图片，不认控制台

**NapCat 在控制台里用字符拼出来的二维码经常扫不出来**——这是 NapCat 自己的已知问题，
它的日志里就写着「如果控制台二维码无法扫码，可以……打开路径图片进行扫码」。

所以启动脚本**始终以图片为准**：

- 二维码一生成（或刷新）就自动用看图程序弹出 `NapCat.Shell\cache\qrcode.png`
- 不依赖 `.png` 的默认关联一定可用，默认关联打不开时会自动退回 **画图 (mspaint)**
- 二维码过期刷新时会重新弹一次（按文件修改时间判断），不会只弹一次就没了
- 即使你用了 `-QQ` 快速登录，万一登录态失效 NapCat 回退到二维码，同样会自动弹图
- 实在都没弹出来，会直接打印图片的完整路径让你手动打开

想单独验证这条路通不通：

```powershell
.\start.bat -TestQr
```

```
==> 测试二维码图片能否弹出
    图片路径 : D:\...\NapCat.Shell\cache\qrcode.png
    图片大小 : 600 字节
    生成时间 : 09/22/2026 12:55:23
    已用「系统默认看图程序」打开，应该能看到二维码窗口了。
```

> 另一个查看途径：NapCat 管理面板 `http://127.0.0.1:6099/webui?token=<token>`
> （token 从 `NapCat.Shell\config\webui.json` 读，脚本失败时会打印完整地址）。

从命令行调用也一样（参数会原样传给 `start.ps1`）：

```powershell
.\start.bat -QQ 3104685327
```

想手动确认环境的话：

```powershell
.\start.ps1 -Status     # 看运行状态
python run.py --check   # 完整自检（DeepSeek + 协议端）
```

看到 `全部通过 ✅` 就说明 DeepSeek 和 NapCat 都通了：

```
---- [3/3] OneBot 协议端连通性 ----
协议端可达: 账号 真红bot(3104685327)
================ 自检结束：全部通过 ✅ ================
```

### 启动脚本到底做了什么

```
1. 检查/创建 .venv 并装依赖（缺什么装什么）
2. 检查 .env 是否存在
3. 已有机器人在跑？直接提示并退出，不会起两份
4. NapCat 已在监听 3001？跳过启动，直接用
5. 启动 NapCat（窗口最小化）；二维码登录时会自动弹出二维码图片
6. 每 3 秒探测一次登录状态（登了才算就绪，服务起来但没登录不算）
7. 登录成功 → 机器人用 pythonw 无窗口启动，NapCat 保持最小化 → 本窗口关闭
```

**成功就转后台，失败就留在前台**——这是刻意的：

| 情况 | 表现 |
| --- | --- |
| 一切正常 | 打印摘要后窗口自动关闭，只在托盘/任务栏留一个最小化的 NapCat |
| 依赖装不上 / 没有 `.env` | 打印原因，窗口停住（`start.bat` 的 `pause`） |
| 登录超时 | 打印最后状态，**把 NapCat 窗口还原到前台**让你看报错，窗口停住 |
| 机器人起来就崩 | 提示去看 `logs\bot.log`，窗口停住 |
| 已经跑着一份 | 提示 `PID xxx 已经在运行`，退出码 3 |

> 想在出问题时第一时间看到原因，加 `-Foreground`：所有日志直接打在窗口里，Ctrl+C 停止。

---

## 后台常驻与开机自启

`start.bat` 已经是后台运行了（机器人用 `pythonw` 无窗口，NapCat 窗口最小化）。

**Linux / macOS** 用 systemd 或 `nohup python run.py > /dev/null 2>&1 &`。

**开机自启**：任务计划程序里新建任务 →
触发器「登录时」→ 操作 `powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File "D:\...\start.ps1" -QQ 3104685327`。

> 注意：`start.bat` 会拒绝重复启动（已有实例时退出码 3），所以开机自启和手动双击不会打架。

### 日志与状态

```powershell
.\start.bat -Status                  # 机器人 / NapCat / 端口 状态
Get-Content logs\bot.log -Tail 30    # 看最近日志
```

---

## 人格设定（需求 1）

当前人格是 **二阶堂真红（复制体）**，写在 `config/persona.md`。

核心设定：**她是二阶堂真红的复制体，不是真红本人。** 记忆与说话方式都继承自本人，
但经历是「知道」而非「走过」，被问起时会平静承认，不会假装自己就是本人。

其余按官方设定来演：外表稚嫩但稳重沉着、冷静地毒舌、爱哭、在意自己 141cm 的身高、
嗜不加糖的黑咖啡、喜欢薄煎饼和狗、讨厌甜点和小孩、低血压爱赖床、21:00 就没力气。

编辑 `config/persona.md`，**保存后下一条消息就生效，不用重启**——
想换性格直接改这个文件就行，比如：

```markdown
## 身份
- 你的名字叫 {bot_name}，是一只常驻 QQ 群聊的猫娘。
## 说话风格
- 每句话结尾加个「喵」。
```

> 注意：`{bot_name}` 来自 `.env` 里的 `BOT_NAME`，**改名字要重启**（人格文件本身是热加载的）。

想给某个群单独设定人格：新建 `config/personas/<群号>.md`，例如 `config/personas/123456789.md`。
该群的所有对话都会优先使用这个文件，其它群不受影响。

可用占位符：`{bot_name}`（机器人昵称）、`{nickname}`（对话者昵称）。
以 `>` 开头的行是**写给人看的注释**，不会送进模型（方便你在文件里写备忘）。

改完可以预览当前生效的人格（注意：带 `>` 的注释行会被自动过滤掉）：

```bash
python run.py --persona
```

---

## 响应规则

### 需求 2：群里只回应 @

```
群友：今天天气不错          → 机器人完全静默（连 DeepSeek 都不调用，不花钱）
群友：@机器人 今天天气如何   → 机器人回复
```

实现细节：机器人从 `self_id` 拿到自己的 QQ 号，检查消息段里有没有 `{"type":"at","data":{"qq":"<自己>"}}`，
并把 @ 段从发给模型的文本里剔除，模型不会看到 `@xxx` 这种噪音。

想让它对所有消息都插嘴（不推荐，容易刷屏）就把 `GROUP_AT_ONLY` 改成 `false`。

### 需求 3 & 4：入群相关

```bash
# 需求 4：有群友加群
WELCOME_TEMPLATE=欢迎 {at} 加入本群！我是{bot_name}，@我就能聊天～

# 需求 3：机器人自己被拉进新群
GROUP_JOIN_GREETING=大家好，我是{bot_name}，一个由 DeepSeek 驱动的聊天机器人。@我就可以开始聊天啦～
```

- `{at}` 会被替换成真正的 QQ @（不是纯文本）
- `{bot_name}`、`{nickname}`、`{group_id}`、`{user_id}` 都会被替换
- `WELCOME_DELAY=1.5`：延迟 1.5 秒发送，等 QQ 的系统提示先刷出来，看起来更像人
- 两条消息留空字符串即为关闭

> ⚠️ **改这些文案要改 `.env`，不是改 `bot/config.py`。**
> 两处都有默认值，但 `.env` 优先级更高，会**静默覆盖** `config.py` 里的默认值。

---

## 互动与定时播报

### 戳一戳

有人戳机器人时（OneBot：`notice_type=notify`、`sub_type=poke`、被戳的 `target_id` 是自己），
机器人会当作「对方戳了你一下」交给模型，用当前人格自然回应。

```bash
POKE_REPLY_ENABLED=true   # 开关
POKE_BACK=false           # true = 顺便用 group_poke / friend_poke 戳回去（可能互相戳个没完）
```

别人互戳不会插嘴。群黑白名单、限流、忽略名单都照常生效。

### 表情包

QQ 的表情包有两种消息段：商城表情 `mface`（带 `summary` 文字摘要），
以及被标记为表情的 `image`。机器人会把它们转成「（发了一个表情包：摘要）」送进上下文，
所以模型能针对表情包的内容回应，而不是只知道"有张图"。

```bash
STICKER_REPLY_ENABLED=true   # 开关
STICKER_RANDOM_CHANCE=0      # 群里没被 @ 时，主动接表情包的概率（0~1）
```

`STICKER_RANDOM_CHANCE` 默认 **0**，也就是只有被 @ 或私聊才回应，符合「仅 @ 响应」的设定。
调到 `0.1` 之类的值会让她偶尔自己插一句，更像真人，但也就打破了「只在该说话时说话」的规则。

> 顺带一提：机器人是纯文本模型，**看不懂图片内容**。普通照片只会得到「（发了一张图片）」，
> 它只能基于这个事实回应，不会真的"看到"图。

### 晚安播报

```bash
GOODNIGHT_ENABLED=true
GOODNIGHT_TIME=23:00            # 24 小时制，默认 23:00（深夜时段起点）
GOODNIGHT_TEXT=晚安，大家。祈祷明天对你来说，也是美好的一天。
GOODNIGHT_JITTER=180            # 0~180 秒随机延迟，避免每天精确到同一秒
```

到点会向**机器人所在的所有群**（黑名单除外）发一句，群与群之间间隔 1.2 秒降低风控概率。

**发失败会重试**（`BROADCAST_RETRIES` / `BROADCAST_RETRY_GAP`，默认 8 次 × 300 秒）：

```bash
BROADCAST_RETRIES=8         # 最多重试 8 轮
BROADCAST_RETRY_GAP=300     # 每轮间隔 5 分钟
```

原来的写法是「到点发一次，失败就等明天」—— 而机器人被风控踢下线的平均间隔才 3 小时，
23:00 撞上掉线窗口的概率并不低，那样一整晚的晚安就只剩一行 ERROR 日志
（NapCat 偶尔回 `retcode=1200 EventChecker Failed` 也是这类静默失败）。
现在改成**只对没发成功的群重试**，发成功的群不会收到第二遍。

> 排查「晚安到底发没发」的时候注意：日志里 **`晚安播报已发送到 3/3 个群`** 才是真发出去了；
> 另外机器人**只发给它在的群** —— 如果群是后来才把机器人拉进去的，那天之前当然收不到。

### 启动问候（按时段自动切换）

启动问候会**读取系统时间**，从五句里挑对应的一句：

| 时段 | 时间范围 | 默认问候语 |
| --- | --- | --- |
| `morning` | 05:00 – 10:59 | 早上好，我苏醒了。希望今天对你来说，也是美好的一天。 |
| `noon` | 11:00 – 12:59 | 中午好，我醒了。今天也请多指教。 |
| `afternoon` | 13:00 – 17:59 | 下午好，我醒了。希望今天对你来说，也是美好的一天。 |
| `evening` | 18:00 – 22:59 | 晚上好，我醒了。希望今晚对你来说，也是美好的一天。 |
| `night` | 23:00 – 04:59 | ……这个点还没睡吗。我也醒了。希望明天对你来说，也是美好的一天。 |

```bash
STARTUP_GREETING_ENABLED=true
STARTUP_GREETING=我苏醒了。希望今天对你来说，也是美好的一天。   # 兜底：分时段留空时用它
STARTUP_GREETING_MORNING=早上好，我苏醒了。希望今天对你来说，也是美好的一天。
STARTUP_GREETING_NOON=中午好，我醒了。今天也请多指教。
STARTUP_GREETING_AFTERNOON=下午好，我醒了。希望今天对你来说，也是美好的一天。
STARTUP_GREETING_EVENING=晚上好，我醒了。希望今晚对你来说，也是美好的一天。
STARTUP_GREETING_NIGHT=……这个点还没睡吗。我也醒了。希望明天对你来说，也是美好的一天。
STARTUP_GREETING_DELAY=6        # 等协议端完全就绪
STARTUP_GREETING_MIN_INTERVAL=0 # 0 = 每次启动都发
```

> ⚠️ **每次启动 = 在每一个群里发一条消息。** 调试期间反复重启会很吵。
> 把 `STARTUP_GREETING_MIN_INTERVAL` 设成 `3600`，一小时内重启就只会发第一次
> （时间戳记在 `logs/.startup_greeting`）。

---

## 群聊氛围感知（不定时插话）

除了被 @ 才回话，机器人还会**每半小时自己读最近 5 条群聊（连同每条的时间），接一句短的**。

```
AMBIENT_ENABLED=true
AMBIENT_INTERVAL=1800      # 每 30 分钟看一次
AMBIENT_MESSAGES=5         # 每次读 5 条
AMBIENT_MAX_CHARS=60       # 接话长度上限
AMBIENT_WINDOW=1800        # 超过 30 分钟的旧消息不算（群里太久没人说话就别插嘴）
AMBIENT_GROUP_WHITELIST=   # 留空 = 所有允许的群；填群号就是只在这几个群插话
AMBIENT_MIN_GAP=120        # 两段主动发言之间至少隔 120 秒，别对着 4 个群连发
AMBIENT_JITTER=120         # 每轮再加点随机抖动
```

送给模型的消息长这样（时间是每条自带的时间戳，所以它能接「这么晚了还不睡」这种话）：

```
（现在是 2026-09-25 02:31（周五深夜），你此刻的状态：困得不行，随时会睡过去）
群里最近这几句：
[02:21] 千里: 中秋快乐
[02:24] Grozovoi: 明天还要上班
[02:25] 知足常乐。: 无妨 晚安吧
[02:26] orb: 晚安安
[02:29] Grozovoi: 我先睡了
你接一句：
```

系统提示里明确写了「这次没有人 @ 你」「不要提聊天记录/时间」「不要 @ 任何人」，
避免它一开口就像个机器人在念摘要。

> ⚠️ **注意发言量**：默认是**每个群**每半小时一句。你现在有 4 个群，
> 也就是每小时最多 8 条主动发言。如果嫌吵，两个办法：
> `AMBIENT_GROUP_WHITELIST=群号1,群号2` 只挑几个群，或者把 `AMBIENT_INTERVAL` 调大。
>
> 另外：主动发言越多，被腾讯风控盯上的概率也越高（见上面的[「老是掉线？先看这里」](#老是掉线先看这里)）。

细节：

- 只有**群里别人发的**消息会被记；机器人自己的发言、`/指令`、图片/表情/语音会记成
  `（发了一张图片）` 这类人话描述（复用 `extract_text`）。
- 某个群最近窗口内不够 5 条就**跳过**，不会对着几天前的话硬接。
- 黑名单群、`AMBIENT_GROUP_WHITELIST` 之外的群一律不参与。
- 插话**不写入会话记忆**，免得把记忆搞得前后不接。

---

## 每日新闻播报

每天 **12:00** 向所有群发一条要闻摘要：**国外 5 条 + 国内 5 条，每条约 20 字**。

```
今日要闻（09-25）
【国外】
· 9名哈萨克斯坦军人在里海军事演习期间遇难
· 荷兰西尼罗病毒疫情蔓延 死亡病例增至5例
【国内】
· 国家对成品油价格实施调控
· 中国知名表演艺术家游本昌去世 享年93岁
```

配置项：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `NEWS_ENABLED` | `true` | 总开关 |
| `NEWS_TIME` | `12:00` | 24 小时制 |
| `NEWS_FOREIGN` | `5` | 国外几条 |
| `NEWS_DOMESTIC` | `5` | 国内几条 |
| `NEWS_ITEM_CHARS` | `20` | 每条大约多少字 |
| `NEWS_JITTER` | `300` | 随机延迟上限（秒），别精确到同一秒 |

手动触发：`/新闻`（别名 `news` / `今日新闻`）。

### 数据源为什么用中新网

新闻源用的是**中新网的 RSS 分栏**，不是聚合 API：

```
国内  https://www.chinanews.com.cn/rss/importnews.xml   （要闻导读）
      https://www.chinanews.com.cn/rss/china.xml        （时政，兜底）
      https://www.chinanews.com.cn/rss/scroll-news.xml  （即时，兜底）
国外  https://www.chinanews.com.cn/rss/world.xml        （国际）
```

选型踩过的坑，别再走一遍：

- **新浪 RSS 是死的**：`rss.sina.com.cn/news/{china,world}/focus15.xml` 返回 200，
  但内容停在 **2018 年**（"光纤之父高锟离世"）。
- **人民网 RSS 也是死的**：停在 **2025-06**（"欧洲央行宣布下调欧元区关键利率"）。
- **中新网是活的**：`pubDate` 就是当天，30 条/栏，纯 UTF-8 XML。
- 参考消息、联合早报、RSSHub 公共实例在国内服务器上**直接连不通**。

抓取链路是「按顺序试兜底链，凑够条数就停」：某个源挂了只记一条 warning，
不影响其它源；某一栏彻底取不到也不阻断播报 —— 只发另一栏，有半份好过一份都没有。

### 「每条约 20 字」是怎么做到的

RSS 标题本来就是编辑写好的短标题，所以**不调 AI，纯本地处理**，没有失败面：

1. 先按原顺序挑**长度已经合适**的（≤ 28 字），避免为了凑数硬砍；
2. 不够才从长标题里裁，裁剪优先在句读（`，、；。！？`）处断开，实在没得断才加 `…`。

想先看看今天会发出去什么，不用等到 12:00：

```bash
python _preview_news.py            # 本地
docker compose exec -T bot python _preview_news.py   # 云端
```

---

## 时间感知

机器人会**读取本机系统时间**（`bot/clock.py`），并在四处使用它：

| 用途 | 说明 |
| --- | --- |
| 启动问候 | 按上面五个时段自动挑选问候语 |
| 模型上下文 | 每轮对话都会把「现在是 2026-09-22 12:47（周二中午），你此刻的状态：精神一般」注入提示词，所以它能自然回答「现在几点」、并在深夜提醒你早点睡 |
| 时间指令 | `@机器人 #现在几点` / `#查询时间` 返回本机时间；`#查询日本时间` 返回指定时区时间 |
| 晚安播报 | 每天 23:00（可配）触发 |
| 每日新闻 | 每天 12:00（可配）触发 |

时段划分（改的话在 `bot/clock.py`）：

```
05:00 - 10:59   早上    刚醒，还有点没力气
11:00 - 12:59   中午    精神一般
13:00 - 17:59   下午    状态正常
18:00 - 22:59   晚上    开始犯困
23:00 - 04:59   深夜    困得不行，随时会睡过去
```

查一下当前该说什么：

```bash
python run.py --now
```

```
系统时间 : 2026-09-22 12:47:27
星期     : 周二
当前时段 : 中午（noon）
状态提示 : 精神一般
问候词   : 中午好
注入模型 : 现在是 2026-09-22 12:47（周二中午），你此刻的状态：精神一般
启动问候 : 中午好，我醒了。今天也请多指教。
晚安时间 : 23:00
```

> 时间全部取自**本机系统时间**，不走网络。所以服务器时区不对的话，
> 问候语和晚安时间也会跟着错——部署到 Linux 服务器时记得设 `TZ=Asia/Shanghai`。

---

## 音游查分（集成提比 Tippy 的查分功能）

数据源用**水鱼查分器（Diving-Fish）**，全是公开接口，**不需要任何 token**。

```bash
RHYTHM_ENABLED=true
RHYTHM_MAX_LIST=8              # /search 一次最多返回几首
RHYTHM_REPLY_CHARS=3500        # 成绩图较长（B50 有 50 行），单独放宽分段阈值
RHYTHM_CACHE_DIR=cache/rhythm  # 曲库缓存目录（24 小时自动过期）
```

### 玩家要做什么

和水鱼查分器一致：到 <https://www.diving-fish.com> 注册、**绑定自己的 QQ 号**、同意用户协议。
之后在群里 `@机器人 /b50` 就能直接出分，机器人不需要做任何账号绑定。

没绑定的情况会明确提示，不会瞎编数据：

```
查不到你的成绩……已设置隐私或未同意用户协议
先去 diving-fish.com 绑定 QQ 并同意用户协议，再回来试试。
```

> 这是踩过的坑：中二查分接口对**未绑定的 QQ 不报错**，而是返回一个
> `rating=0`、`b30/n20/r10` 全空的占位账号。直接照抄会把假成绩显示给用户，
> 所以代码里专门识别这种情况并转成友好提示。

### 指令

群里需要先 @ 机器人（沿用「仅 @ 响应」规则）。**命中音游指令时不会调用 DeepSeek**，
既不消耗额度也更快。

```
@真红bot /search 林檎          → 查歌
@真红bot /id 834               → 歌曲详情
@真红bot /score 林檎           → 我的舞萌单曲成绩
@真红bot /b50                  → 舞萌 Best 50（旧曲 B35 + 新曲 B15）
@真红bot /b40  /ap50           → Best 40 / AP 50
@真红bot /cscore BBK           → 我的中二单曲成绩
@真红bot /chub30               → 中二 Best 30
@真红bot /定数表 13+           → 舞萌定数表
@真红bot /中二定数表 14+       → 中二定数表
@真红bot /jrys                 → 今日运势（同一天同一人结果稳定）
@真红bot /roll 打游戏 看电影   → 帮你选一个
@真红bot /音游帮助             → 指令列表
#查询日本时间                  → 世界各地时间（这条不需要 @）
```

搜索是**去标点**的，所以 `BBK` 能命中 `B.B.K.K.B.K.K.`，`m834` 也能命中 `834`。

### 排错

用真实 QQ 号验证接口返回结构与解析逻辑是否一致：

```bash
python run.py --probe-qq <你的QQ>
```

曲库拉不下来时先删缓存重试：

```bash
Remove-Item -Recurse -Force cache/rhythm
```

> 时区查询依赖 `tzdata` 包（Windows 没有系统时区库），已在 `requirements.txt` 里。

---

## 记忆与限流

```bash
# 记住最近 12 轮问答，1 小时没说话就忘掉
HISTORY_MAX_TURNS=12
HISTORY_TTL=3600

# shared  = 整个群共享一份上下文（能看到彼此说了什么，像真的在群聊）
# per_user = 每个人独立上下文（互不干扰，像私聊）
GROUP_MEMORY_MODE=shared

# 每人 60 秒最多问 5 次，全机器人 60 秒最多 60 次
RATE_LIMIT_PER_USER=5
RATE_LIMIT_WINDOW=60
RATE_LIMIT_GLOBAL=60
```

## 群权限

```bash
# 只服务这两个群（留空 = 所有群）
GROUP_WHITELIST=123456789,987654321
# 这几个群永远不理（优先级更高）
GROUP_BLACKLIST=111111111
# 不理会指定 QQ（比如群里其它机器人）
IGNORE_USER_IDS=123456,654321
```

---

## 内置指令

在群里 @ 机器人 或私聊发送：

| 指令 | 作用 |
| --- | --- |
| `重置对话` / `/reset` | 清空当前会话记忆 |
| `帮助` / `/help` | 功能说明 |
| `状态` / `/status` | 查看当前模型、人格文件、记忆条数 |

---

## Docker 一键部署

`docker-compose.yml` 会同时拉起 NapCat 和机器人：

```bash
cp .env.example .env
# 编辑 .env，填 DEEPSEEK_API_KEY，并把 ONEBOT_WS_URL 改成 ws://napcat:3001
docker compose up -d

# 查看 NapCat 登录二维码
docker compose logs -f napcat
```

NapCat 的 WebUI 在 <http://127.0.0.1:6099>，扫码登录后即可使用。

---

## 老是掉线？先看这里

### 现象

机器人隔一阵就"消失"，NapCat 日志里能看到：

```
[KickedOffLine] [下线通知] 你的账号当前登录已失效，请重新登录。
账号状态变更为离线
[Core] [Login] 账号被踢下线，正在重启 Worker 以重新创建 QQ 登录服务
```

被踢之后 NapCat 只能重新生成二维码，没人扫就一直离线 —— 所以从群友视角看，就是"机器人死了"。

### 这是腾讯风控，不是代码问题

在阿里云这类**机房 IP** 上用非官方客户端（NapCat）登录 QQ，会被腾讯风控周期性踢下线。
实测数据：**29 小时内被踢 10 次，平均在线 3.2 小时，最短 14 分钟**。同期容器
`RestartCount=0`、`OOMKilled=false`、内存富余，被踢前 15 分钟机器人**一条消息都没发**，
所以跟内存、崩溃、发消息频率、双端抢登都无关。

社区已有大量同环境复现，见
[NapCatQQ Issue #1728](https://github.com/NapNeko/NapCatQQ/issues/1728)。
结论大致是：**Docker 版 NapCat 最容易被踢**，换非 Docker 的 Linux Launcher 并开启反检测
有人能稳定一周以上；早期"降级到 4.15.x"的方案现在已失效（内置 QQ 版本太旧，腾讯禁止登录）。

> 注意：持续被风控可能升级为**封号**。请务必用小号，并配合下面的自愈手段。

### 保底手段 1：快速登录 + 重启自愈（实测有效）

`docker-compose.yml` 里已经打开：

```yaml
environment:
  - ACCOUNT=${BOT_QQ:-}     # .env 里写 BOT_QQ=你的机器人QQ号
```

它会让 NapCat 用 `qq --no-sandbox -q $ACCOUNT` 启动。

实测数据点（2026-09-25）：

| 操作 | 结果 |
|---|---|
| 掉线后干等（不重启容器） | QR 每 2 分钟重生一次，一直离线 |
| `docker restart napcat` | **21 秒后 3001 恢复 LISTEN，不需要扫码** |

日志长这样，`快速登录` 后面**没有** `快速登录错误` 就是成功了：

```
01:30:48 [info] [NapCat] [Core] NapCat.Core Version: 4.18.28
01:30:49 [info] 正在快速登录  3104685327
01:30:50 [info] [OneBot] [WebSocket Server] Server Started :::3001
```

> 日志里如果出现 `快速登录错误： 登录态已失效，请重新登录。` 也不用紧张 ——
> 那只是 `-q` 这一条路径失败，Worker 往往还会用本地会话数据自己登回来
> （实测有过「打了这行错误，但 1 秒后 3001 就起来了」的情况）。
> **真正可靠的判据是 3001 有没有 LISTEN，不是这行日志。**

### 保底手段 2：掉线看门狗（推荐）

`scripts/napcat-watchdog.sh` 由 systemd timer 每分钟跑一次：

| 情况 | 动作 |
|---|---|
| 掉线 | `docker restart napcat` 自动恢复，最多 3 次（每次间隔 150 秒，够它登回来） |
| 3 次都救不回来 | 记录 + 通知（可配 webhook）+ 等你人工扫码，期间只写心跳 |
| 恢复在线 | 自动重置状态 |

探活判据是**容器内 3001 是否 LISTEN**。注意 napcat 容器里**没有 `ss`/`netstat`/`lsof`**
（早期版本用 `ss` 判断，结果永远返回"离线"，是个真 bug），现在改成读 `/proc/net/tcp`：

```bash
grep -i ':0BB9 ' /proc/net/tcp /proc/net/tcp6 | grep -q ' 0A '   # 0BB9 = 3001
```

OneBot 的 WS 服务是 QQ 登录成功之后才起来的，所以这个判据等价于"已登录"。

装在服务器上（需要 root）：

```bash
sudo bash scripts/install-watchdog.sh          # 安装 + 立即跑一轮
bash scripts/napcat-watchdog.sh --status       # 看登录状态 / 被踢记录 / 当前二维码链接
bash scripts/napcat-watchdog.sh --reset        # 清状态（一般不用手动做）
sudo bash scripts/install-watchdog.sh uninstall
```

日志在 `logs/watchdog.log`。

想让它主动通知你：把 webhook 地址写进项目根目录的 `.watchdog-webhook`（一行），
掉线时会 POST `title=真红bot 掉线&desp=...&text=...`（Server酱 / PushPlus 之类的表单接收端直接可用）。

### 保底手段 3：重新扫码

```bash
# Linux/macOS
docker cp napcat:/app/napcat/cache/qrcode.png ./qr.png && xdg-open ./qr.png
```
```powershell
# Windows（本仓库附带的辅助脚本）
.\.ssh\qr-loop.ps1        # 扫码成功前一直刷新最新二维码到屏幕上
```

二维码约 2 分钟过期，过期会自动重新生成，重新拉一次即可。

### 想真正少被踢

**第 1 步（最重要，已经做了）：打开反检测**

NapCat 自带一页「反检测配置」，对应 `napcat.json` 里的 `bypass` 字段，
控制 Napi2Native 模块的各项反检测能力：

| 字段 | WebUI 标签 | 含义 |
|---|---|---|
| `hook` | Hook | hook 特征隐藏 |
| `window` | Window | 窗口伪造 |
| `module` | Module | 加载模块隐藏 |
| `process` | Process | 进程反检测 |
| `container` | Container | **容器反检测**（跑 Docker 时最相关） |
| `js` | JS | JS 反检测 |

**Docker 镜像自带的模板里这六个全是 `false`，也就是反检测全关。** 一键打开：

```bash
bash scripts/enable-anti-detection.sh          # 打开并自动重启 NapCat
bash scripts/enable-anti-detection.sh show     # 看当前值
bash scripts/enable-anti-detection.sh off      # 关掉
```

⚠️ 注意 NapCat 真正加载的是**按账号**的那份 `data/napcat/config/napcat_<QQ>.json`
（日志里的 `[Core] [Config] 配置文件...加载`），全局 `napcat.json` 只是模板 ——
脚本两个都会改。改完看日志确认生效：

```
[Core] [Config] 配置文件/app/napcat/config/napcat_3104685327.json加载 {"o3HookMode":1,
 "bypass":{"hook":true,"window":true,"module":true,"process":true,"container":true,"js":true}}
```

**第 2 步：如果还是频繁被踢，再考虑换部署方式**

1. **换非 Docker 部署**：用 NapCat Linux Launcher 原生跑最新版
2. **换住宅 IP**：把协议端放在家里电脑上，云服务器只跑机器人
3. ⚠️ 不要指望降级 NapCat 版本 —— 旧版内置的 QQ 已被腾讯禁止登录
4. ⚠️ 社区里也有人反馈**换非 Docker 后照样频繁掉线**
   （[#1728](https://github.com/NapNeko/NapCatQQ/issues/1728) 里两种案例都有），
   所以这属于「值得一试但不保证」，别期待值拉太满

### 怎么判断反检测有没有用

看 NapCat 日志里 `KickedOffLine` 的间隔。改造前的基线是：

| 指标 | 改造前 |
|---|---|
| 被踢次数 | 28.8 小时内 10 次 |
| 平均在线时长 | 3.20 小时 |
| 最短 / 最长 | 14 分钟 / 9.2 小时 |

```bash
# 一行看被踢次数
docker logs napcat 2>&1 | grep -c KickedOffLine
```

---

## 项目结构

```
qq-deepseek-bot/
├─ run.py                  # 启动入口（--check 自检 / --persona 预览人格）
├─ requirements.txt
├─ .env.example            # 配置模板（复制成 .env）
├─ config/
│  ├─ persona.md           # ★ 全局人格设定，改这个就行
│  └─ personas/            # 群专属人格：<群号>.md
├─ bot/
│  ├─ config.py            # 配置加载 + 人格热加载
│  ├─ clock.py             # ★ 系统时间：时段判定与问候语选择
│  ├─ onebot.py            # OneBot v11 客户端（WS 收发、重连、CQ 解析）
│  ├─ deepseek.py          # DeepSeek 客户端（SSE 流式 + 重试）
│  ├─ memory.py            # 多轮记忆（按会话隔离、TTL/LRU 裁剪）
│  ├─ ratelimit.py         # 滑动窗口限流
│  ├─ app.py               # ★ 核心：事件分发 / @唤醒 / 入群欢迎 / 生成回复
│  ├─ log.py
│  └─ rhythm/              # ★ 音游查分
│     ├─ sources.py        #   水鱼数据源、曲库缓存、去标点检索
│     └─ commands.py       #   指令解析与成绩渲染
├─ tests/
│  ├─ test_e2e.py          # 端到端自测（本地假 NapCat + 假 DeepSeek，不花额度）
│  └─ test_rhythm.py       # 音游模块离线自测（固定数据，不联网）
├─ Dockerfile
├─ docker-compose.yml
├─ _preview_news.py         # 真连一次新闻源，预览 12:00 会播出去什么
├─ scripts/
│  ├─ napcat-watchdog.sh         # ★ 掉线自愈看门狗（systemd timer 每分钟跑）
│  ├─ install-watchdog.sh        #   安装/卸载上面那个 systemd 服务
│  └─ enable-anti-detection.sh   # ★ 打开 NapCat 内置反检测（bypass 开关）
├─ start.bat               # ★ 双击启动（NapCat + 机器人，登录后自动转后台）
├─ stop.bat                # ★ 双击停止（只关自己启动的进程）
├─ start.ps1 / stop.ps1    #   上面两个 bat 的实际实现
├─ start.sh                # Linux/macOS 用手动方式启动
├─ NapCat.Shell/           # QQ 协议端本体（含下面这份已配好的 OneBot 配置）
│  └─ config/
│     └─ onebot11_3104685327.json   # ★ 正向 WebSocket 服务：127.0.0.1:3001
├─ cache/rhythm/           # 曲库缓存（24 小时自动刷新，可安全删除）
└─ logs/bot.log            # 运行日志
```

---

## 自测

不需要真实 API Key、不需要 QQ 号、不联网，直接跑：

```bash
python tests/test_e2e.py      # 主流程，70 项断言
python tests/test_rhythm.py   # 音游 + 时间模块，81 项断言
```

`test_e2e.py` 会在本地起一个「假 NapCat」（OneBot WebSocket 服务端）和「假 DeepSeek」（SSE 接口），
覆盖 18 组场景：未 @ 沉默、@ 后回复、多轮记忆、群共享上下文、重置、私聊、
黑名单、限流、入群打招呼、新人欢迎、消息去重、撤回遗忘、长文本分段、
Markdown 清洗、群专属人格、API 故障降级、启动问候、戳一戳、表情包、定时播报。

`test_rhythm.py` 用固定数据测音游与时间模块：曲库解析容错（舞萌的 `notes` 是数组、
脏数据不拖垮整库）、去标点检索、B50/B30 渲染、未绑定识别、时段边界划分、
启动问候按时段切换、以及「非指令必须原样交还给大模型」这条关键边界。

真实接口的连通性另外验证：

```bash
python run.py --probe-qq <你的QQ>
```

---

## 常见问题

**安装 NapCat 时报 `HTTP状态码: 404` / `下载QQ失败`**
NapCat 内置的 QQ 下载链接已失效（腾讯轮换了带哈希的路径）。别用一键包，
改成「第 1 步 · 方式 A」：去 QQ 官网自己下最新版 QQ，再配 `NapCat.Shell.zip`。

**连不上协议端 / 一直重连**
1. 确认 NapCat 已登录 QQ，且「WebSocket 服务器」是启用状态
2. 确认 `.env` 里的 `ONEBOT_WS_URL` 端口与 NapCat 里配置的一致
3. 如果 NapCat 设置了 Token，`.env` 里的 `ONEBOT_ACCESS_TOKEN` 必须完全一致
4. 跑 `python run.py --check` 看具体报错

**机器人隔几小时就掉线一次**
这是腾讯风控在踢号，不是代码问题 —— 见上面的[「老是掉线？先看这里」](#老是掉线先看这里)，
装 `scripts/install-watchdog.sh` 让掉线至少能被发现、能自愈。

**机器人不回复**
1. 群里必须 @ 它（且 @ 的是真的那个 QQ 号）
2. 看控制台有没有 `未 @ 机器人，保持沉默` 的 debug 日志 —— 把 `LOG_LEVEL` 调成 `DEBUG` 就能看到判定过程
3. 检查是否被 `GROUP_WHITELIST` / `GROUP_BLACKLIST` 拦了
4. 私聊不回的话看 `PRIVATE_ENABLED` 是不是 `false`

**DeepSeek 报 401**：API Key 填错了，或者没充值（余额为 0 也会 401/402）。

**回复太长被截断**：调大 `DEEPSEEK_MAX_TOKENS`；发出去被切段是 `MAX_REPLY_CHARS` 控制的，属正常行为。

**想让不同群有不同性格**：在 `config/personas/` 下建 `<群号>.md`。

**机器人会不会乱说话 / 被滥用**：设好 `GROUP_WHITELIST` 只服务自己的群，配合 `RATE_LIMIT_*` 限流，人格文件里写清楚行为边界。

---

## 免责声明

本项目的 QQ 接入依赖第三方协议端（NapCat）。使用非官方客户端登录 QQ 存在账号被限制的风险，
请使用小号测试、遵守腾讯的服务条款，风险自行承担。
=======
# Private-QQ-Chat-Bot
developed by napChat, combined with deepseek api, rhythm game score checking feature added
>>>>>>> 4d4c08b2679cef5bb2513e822acfb6e6be099eec
