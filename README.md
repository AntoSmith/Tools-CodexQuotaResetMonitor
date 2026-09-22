# Codex 额度重置提醒

当前使用浏览器采集，唯一需要编辑的配置是 `config.browser.local.toml`。不需要 X_BEARER_TOKEN 或付费 API 预算。捕捉到的内容是候选线索，不保证适用于你的订阅。

## 首次使用

需要 Python 3.11+、Node.js/npm，以及本机安装的 Chrome 或 Edge。Windows 桌面启动器使用 .NET Framework；后台隐藏浏览器功能仅适用于 Windows。

在项目目录执行：

```powershell
Copy-Item config.browser.example.toml config.browser.local.toml
npm install
```

编辑 `config.browser.local.toml`：核对需要监控的公开账号后把其 `verified` 改为 `true`，不需要的账号段可以删除。模板默认 `channel = "console"`，只输出日志；需要邮件时按下文填写真实邮箱和授权码，保留原数据库路径即可。真实配置不要写入 example 模板。

首次登录 X：先在监控窗口点击“停止监控”，再双击项目目录的 `LoginX.cmd`。在打开的专用浏览器中手动登录，确认能打开账号的 Replies，然后关闭全部专用浏览器窗口，回到监控程序点击“启动监控”。以后后台复用同一个 `browser.profile` 保存的会话，失效后重新执行此步骤。日常 Chrome 中已经登录，不代表这个独立配置也已登录；不要更换或清理 profile 目录。登录入口本身不读取帖子、不创建通知，不验证密码或登录是否成功。

先运行 `python -m quota_monitor browser-once` 检查采集，再按下文构建 EXE 或执行 `python -m quota_monitor browser-run`。Python 核心使用标准库，无需安装额外 Python 依赖；仅重新生成 Logo 时需要 Pillow。

准备上传源码请阅读 [GitHub 发布说明](docs/PUBLISH.md)。仓库只包含配置模板，不包含个人配置、日志、浏览器登录数据或编译后的 EXE。

## Windows 桌面启动器

主窗口右上角“设置”可开关“登录 Windows 后自动启动监控”，点击“保存设置”生效。“修复启动项”会重新登记当前程序；“Windows 启动应用”可检查系统是否允许启动。设置显示“已登记”只证明注册项存在，不保证安全软件或系统一定放行。自启动默认只显示托盘图标。

启动诊断保存在 `logs/desktop-startup.log`：`launch mode=autostart` 表示已进入自启动入口，`monitor_started` 表示采集进程已启动，`monitor_start_failed`/`fatal` 表示启动失败，`existing_instance` 表示已有实例。日志以追加方式保留，供关机、登录后排查；不要仅凭主窗口没弹出判断未启动。

项目专属 Logo 使用青绿色重置环与白色代码括号，统一用于窗口、托盘和 EXE。矢量源图为 `assets/quota-monitor.svg`，多尺寸 Windows 图标为 `assets/quota-monitor.ico`；图标已嵌入 EXE。若需重新生成素材，可运行 `python scripts/generate-logo.py`（仅生成素材时需要 Pillow）。

双击项目目录中的 `CodexQuotaMonitor.exe` 即开始监控，无需手动输入 Python 命令。右下角托盘图标可查看状态、启动/停止监控、打开配置与日志，或切换“登录 Windows 自动启动”。关闭状态窗口会收回托盘；右键托盘选择“退出”才会停止程序及它启动的采集进程。

启动按钮会保持深色“监控中”，直到停止或进程退出。窗口区分准备采集、正在采集和等待下一轮：浏览器完成一轮后关闭，监控继续等待，并显示倒计时。本次启动的已结束轮数包含失败和空结果，另行显示失败/空结果数量；开始/结束时间按本机时区显示。重新启动监控后轮数归零，中途停止的轮次标为中断，不算已结束。

结果提示对应当前轮次。下一轮开始时清除旧结果、旧结束时间与橙色告警，显示“第 N 轮采集中”；成功后显示本轮帖子数，累计失败次数保留。空结果会尽量注明具体原因，例如“读取到帖子，但 N 条因发布时间无法识别被跳过”。后台采集统一页面语言和 UTC 时区，同时兼容页面显示的中文完整日期。

捕获到新的、仍在时效内的候选线索，会弹出桌面提醒，即使 SMTP 投递失败也会显示。同一帖子版本不会每轮重复弹出；已通知和过期线索不补弹。规则更新后，会重新检查仍在时效内且之前被忽略的最新已观察帖子版本，命中则补提醒一次。弹窗展示分类、账号、原文和查看原帖按钮，多个提醒依次展示；最近一条可以从主窗口再打开。重置候选/重置券提醒有约三秒的烟花效果；待核实、撤回和一般额度变化采用平静样式。候选不代表你的账户额度已恢复。

监控账号的正文出现重置线索，即使省略 Codex 或额度字样，也会作为“待核实”提醒，避免遗漏简短后续公告；明确的密码、设备等无关重置继续排除。Astra 名称、引用内容及未明确时区的“午夜”不会被自动解释成确定的 Codex 重置范围或倒计时。

点击“预览提醒”可随时查看标明“演示”的弹窗与动画，不会抓取推文、发邮件或改动去重记录。真实提醒弹出时可以先关闭它，再使用预览。关闭提醒不会停止监控。

登录自启动使用当前用户的 Windows Run 启动项，不需要管理员权限；电脑必须联网且保持唤醒。仍按配置每 30 分钟采集一次。Windows 默认隐藏采集浏览器，不抢占工作桌面；进度和提醒继续在监控界面显示。抓取与邮件投递结果请查看 `logs/desktop-monitor.log`。

修改配置后，先停止监控，再启动以加载新配置。需要执行 `test-email` 时也先从托盘停止监控。请勿同时另开 `browser-run`；程序会拒绝占用同一数据库的第二个监控进程。

这是本机启动器，需要与 `quota_monitor`、`scripts`、配置和 `data/desktop` 保持在同一个项目目录，并依赖本机 Python、Node.js、Playwright 和 Chrome。不能只复制 EXE 到另一台电脑使用。邮箱授权码仍保存在本地配置中，没有编进 EXE。移动整个项目之前，先取消自启动，移动后重新构建并启用自启动。

修改启动器源码或更换 Python/Node 路径后，先退出托盘程序，再重新构建：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-desktop.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tests/test-desktop.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tests/test-desktop-feedback.ps1
```

构建使用 Windows 自带的 .NET Framework C# 编译器；测试只使用本地模拟进程，不发送邮件。执行策略参数仅对这次 PowerShell 进程生效。

`test-desktop.ps1` 包含 `--ui-test`，验证按钮、进程会话隔离及退出时最后的状态消息，并将实际窗口截图写到 `data/desktop/dashboard-preview.png`、`data/desktop/alert-preview.png`。采集生命周期使用 `@quota-ui` JSON 事件同步界面；普通控制台通知写入 stderr，仍记录在桌面日志里。

## 浏览器实验模式（无需付费 X API）

浏览器模式读取公开页面，不保证覆盖完整。网站可能限制无头浏览器或未登录访问，因此默认使用完整浏览器；是否能够读取须在自己的网络和浏览器环境中验证。

复制 `config.browser.example.toml` 为本地配置后使用；通知方式取决于 `notifications.channel`，`smtp` 为邮件，`console` 为日志输出。运行 `npm install` 安装 package.json 指定的 Playwright。默认使用已安装的 Chrome/Edge，不需要另外下载 Playwright Chromium。也可设置 `browser.node_modules` 指向已有依赖目录。

```powershell
# 在下载或克隆后的项目目录运行：
python -m quota_monitor browser-once --config config.browser.local.toml
# 排查登录/验证问题时，显式显示浏览器（同时覆盖 background 和 headless）：
python -m quota_monitor browser-once --config config.browser.local.toml --headed
# 确认本机可以正常读取后再持续运行（Ctrl+C 停止）：
python -m quota_monitor browser-run --config config.browser.local.toml
# 手动登录专用浏览器，窗口一直保留到你关闭（先停止监控）：
python -m quota_monitor browser-login --config config.browser.local.toml
```

`browser-once` 优先打开所配置账号的 Replies（`/with_replies`），再访问主页和有限数量详情页，保存报告与合格帖子，不发送通知。它会生成待处理 outbox；以后启动 `browser-run` 时，仍在有效期内的候选会投递，相同版本不会重复入队。没有可用帖子时退出码为 1，不表示账号没有发帖。

新版模板启用 `[browser] reset_feed_enabled = true`：先从 [codex-reset.com 的公开接口](https://codex-reset.com/developers)发现 Tibo 帖子和回复链接，不需要登录 X 或提供 X API Key。该接口是第三方社区服务，按当前 30 分钟周期读取；本地缓存至少 60 秒并遵守 429 的 Retry-After。`reset_feed_user_agent` 标识请求项目；公开部署请改成带真实项目联系网址的值，如 `MyMonitor/1.0 (+https://example.com/project)`。Data: [codex-reset.com](https://codex-reset.com/)。

有有效候选时跳过 Tibo 主页与 Replies 的发现步骤，直接读取头部候选的 X 详情；原文明示 reset 的近期帖子最多优先占一半预算，其余按新旧选择，合计不超过 `max_posts`。接口还包含经过筛选的帖子，因此不能认为它覆盖所有互动。新鲜度要求 `stale=false`、上游抓取不超过 15 分钟、发布时间仍在 `max_age_hours` 内；不使用预测概率、网站事件标签或旧的 active signal 直接发提醒。接口关闭、出错、过期或无可用候选时回退原公开页面流程，其他监控账号保持原流程。

回复详情没有所指父帖时，可按接口的父帖 ID/作者再访问一页。父帖必须 ID、作者一致且正文完整，才纳入上文；关系标为第三方所报，未独立核实。每条候选最多补读一个父帖；读取失败会记录 `context_status=parent_unavailable`。`context_enabled=false` 会关闭上下文及父帖访问。X 回复本身无法读取时记录跳过，不采用第三方正文代替，也不在本轮无限增加页面重试。

邮件与弹窗保留 Data: codex-reset.com 链接和双方原文。本次第三方只用于发现与定位，正文、发布时间仍取自 X DOM，避免第三方秒级时间、换行或 @ 前缀差异制造重复提醒；上文补全不会改变作者版本。报告中的 `reset_feed`、`discovery_source`、`context_status` 显示实际来源和缺失情况。`test-email` 的最新主页帖测试和 `browser-login` 不调用该接口。

默认启用 `[browser] context_enabled = true`：在监控作者自己的帖子详情页，最多读取前面连续的 3 条完整帖子作为上文候选。引用卡片、正文后方评论、主页上相邻帖子不作为上文；页面位置不能证明直接回复关系，所以通知会分别标注作者原文、上文作者及链接，并注明关系未核实。例如上文说“you owe us a banked reset”，Tibo 回应“OK fine … Tuesday”，会作为“待核实”线索提醒，不据此确认重置范围或生成周二倒计时。后来其他用户的解读不会作为 Tibo 的承诺。设置 `context_enabled = false` 可关闭此功能。

若 Replies 受限，也可以在 `[browser]` 添加 `conversation_urls = ["https://x.com/用户名/status/数字ID"]`，最多 5 个公开对话链接。程序从这些页面寻找**已配置监控账号**的发言，再访问该发言本身的详情页。每账号最多将一半详情预算用于这些指定对话，其余保留给最新帖子；`max_posts = 1` 时只选发现的最新帖子。过期或不再相关的链接应移除，避免增加访问耗时。关闭上下文或使用最新帖邮件测试时，不扫描指定对话。页面未公开展示的回复仍可能漏报，不能保证完整覆盖。

程序直接读取浏览器渲染后的 DOM，无需截图识别。仅请求原始 HTML 在本机实测可返回页面外壳但没有推文正文，暂不能替代浏览器。滚动后没有新帖子 ID 时会提前停止；报告增加 `duration_seconds`（本轮采集耗时）和 `pages_visited`（导航尝试次数），便于观察耗时。`max_scrolls`、`max_posts` 和总超时仍限定工作量。仅补全上文不会重复发送已提醒的同版本帖子；此前忽略的帖子补全上下文后，可在 `max_age_hours` 内提醒一次，过期帖子不补发。

从已打开的浏览器复制的 HTML 可能包含已加载的 Replies，但只是该时刻的页面快照，不能替代持续采集所需的登录状态。解析器会排除已识别的沉浸式翻译译文节点，保留作者原文（包括原本用中文发布的内容），避免将插件译文混入判断。

`browser-run` 默认每 30 分钟一轮，通知线程独立检查队列。电脑休眠、程序退出或页面打不开时无法采集。桌面启动器在后台调用同一个命令。浏览器模式不创建 X API 客户端，不检查 X Token/付费预算。SMTP 仍需授权码；切换通知通道或增删收件人无需更换 database。

头部抽样的默认设置是 `interval_minutes = 30`、`max_scrolls = 1`、`max_posts = 5`：先看 Replies 再看主页，各最多向下滚动一次，从发现的帖子中按 ID 新旧选择，每账号合计最多访问 5 条详情；指定对话链接沿用前述预算分配。不遍历全部历史回复。若半小时内发帖超过预算，或页面排序/访问受限，仍可能漏报；可按需要增加上限。`max_scrolls = 0` 则只读取初始已加载内容。

Windows 下默认 `browser.background=true`、`headless=false`：完整浏览器在独立的不可见 Windows 桌面启动，完成后清理，既不显示在工作桌面，也不会将窗口激活到前台。它不是 Windows 的 Win+Tab 虚拟桌面，不会切换你的工作桌面。Playwright 仅通过本机临时调试端口连接这次创建的浏览器；后台启动失败会报错，不回退到前台模式。

需要登录或完成验证码时，先停止监控，再双击 `LoginX.cmd` 或运行 `browser-login`，它会等待你主动关窗。`browser-once --headed` 只适合观察试采集，结束会自动关窗。`headless=true` 仍是另一个无界面模式，本机此前遇到 403，因此当前继续保留完整浏览器模式。错误报告包含失败阶段、HTTP 状态码等，不会把失败解释为没有新帖。`browser-once` 不发送通知，但生成的候选可能在后续 `browser-run` 中发送。

报告保存在数据库同目录的 `last-browser-report.json`，当前邮件监控为 `data/browser-mail/last-browser-report.json`，含覆盖、错误、跳过原因及取得的原文，不宜公开提交。状态查看：`python -m quota_monitor status`。

登录墙、验证页、有限公开时间线、置顶帖、排序和滚动上限都可能导致漏报。只处理作者正文清晰且有明确发布时间的完整详情，其他情况跳过；不以“几小时前”猜时间，不绕过验证码。详情时间通过英文界面、UTC 时区读取，并兼容中文完整日期，精度可能只有分钟。数字作者 ID 来自配置映射；没有 API 身份复核、回复链和编辑历史，不能可靠撤销旧提醒，所以示例关闭提前提醒。省略产品名称的重置线索归为待核实，具体范围须人工确认。读到若干帖子不证明覆盖完整。

## 启动与停止

优先使用上面的 EXE 托盘入口。也可以运行 `./scripts/run.ps1`，它已改用浏览器模式和当前配置。命令行运行时保持电脑联网、不休眠；Ctrl+C 停止。

## 邮件条件与配置

### 单封测试邮件

填好 `[notifications]` 下的 `QQ_SMTP_AUTH_CODE`、`username` 和 `to` 后运行：

```powershell
python -m quota_monitor test-email
```

默认抓取 Tibo 主页当前可见的最新帖子，跳过重置关键词筛选，通过 SMTP 发送一封标明“工作流测试”的邮件。即使 `channel = "console"`，这个测试命令也会发送，但不会改动配置、日常去重记录或处理日常待发队列。每次手动执行会重新发送一封。

只预览、不发送：`python -m quota_monitor test-email --preview`。指定其他已配置账号：`python -m quota_monitor test-email --account OpenAI`。预览和最近一次发送结果保存在数据库同目录的 `last-email-test.json`，不包含授权码。

先按 Ctrl+C 停止正在运行的 browser-run，再运行测试，避免占用同一浏览器配置。最新帖详情无法读取时不使用旧缓存，也不退回发送更旧的帖子。页面覆盖有限，只能保证是主页当前可见内容中的最新一条。

出现“SMTP 服务器已接受”后检查邮箱或垃圾邮件；这不等于已确认进入收件箱。发送异常时不会自动重试，以免不确定结果下重复发送。

### 日常监控邮件

邮件同时包含 HTML 卡片与原始纯文本。HTML 显示提醒类型、判断依据、易读的北京时间、原文/上下文和原帖按钮，保留来源与待核实说明；不支持 HTML 的客户端可以显示纯文本。不同邮箱客户端的排版可能略有差异。

离线查看演示模板：`python -m quota_monitor.email_preview`。输出 `data/email-preview/email-demo.html` 和 `.eml`，均使用显著标注的合成数据；不读取个人配置、不联网、不发送邮件。可在浏览器打开 HTML 或在邮件客户端打开 EML。演示不代表真实重置公告或实际收件记录。

桌面用户可以点击右上角 **设置 → 收件人**，在表格中逐行填写 **备注名称（可选）+ 邮箱地址**，例如“我的主邮箱 / first@example.com”“工作通知 / second@example.com”。点击“添加收件人”增加一行，“删除选中行”移除一行，最后点击 **保存并应用**。备注支持中文，最多 80 字；每行一个邮箱。相同邮箱自动去重；若重复邮箱使用不同的非空备注，会提示合并为一行。

首次打开会自动带入现有邮箱，旧名单的备注留空。保存后同步到程序实际读取的配置，监控正在运行时会重启采集进程加载新名单，当前采集轮次会中断；已停止时只保存，下次启动生效。保存收件人不会改变自启动选项，也不会自动发送测试邮件。名字仅作本地备注，投递和去重仍按邮箱地址，修改备注不会补发历史邮件。

界面名单和备注统一保存在 `data/desktop/config.browser.local.toml.notifications.json`，该目录已排除上传。v2 格式保存 `contacts`（每项包含 `name`、`email`）；读取时自动生成 `notifications.to` 和 `notifications.recipient_names`，前者供现有发送逻辑使用，后者是“邮箱 → 备注”映射。无需维护两份名单，也无需手动修改 TOML。桌面和使用此配置的命令行读取同一份设置，其他配置文件使用各自的本机名单。旧版 v1/分号字符串兼容读取。发件邮箱、授权码和 `channel` 仍沿用原配置；若是 `console` 模式，设置窗口会明确提示仍不发邮件。

支持多个收件邮箱，例如：

```toml
[notifications]
channel = "smtp"
username = "sender@example.com"
to = "first@example.com;second@example.com;third@example.com"
QQ_SMTP_AUTH_CODE = ""
```

`username` 仍为单个发件邮箱。`to` 推荐用英文分号 `;` 分隔，也支持中文分号 `；`，兼容中英文冒号和逗号。允许地址两侧有空格，重复地址只投递一次；不要留空项或换行。每个收件人单独发送邮件，不在 To 头公开其他收件人。日常队列保存各邮箱的成功状态，部分失败时仅重试尚未记录成功的邮箱；SMTP 响应丢失或进程在接受后、记录前退出时，仍可能重复投递。

收件人数没有固定为两个，可以填写一个、三个或更多，例如 `to = "first@example.com;second@example.com;third@example.com"`。增删地址后停止并重新启动监控即可生效，保留原 `[monitor].database` 和历史去重记录，不会因为名单改变而拒绝启动。

每条通知保存创建时的收件人名单。新增邮箱只接收之后的新通知，不补发历史消息；删除的邮箱不再接收排队中的通知（含延后提醒），即使再次添加也不恢复旧消息。仍在名单中的邮箱继续重试自己的未成功投递，已成功的不重发。切换 console/smtp 通道会取消旧通道尚未发送的队列，新通知使用新通道。旧数据库首次运行新版时自动兼容，无需手动迁移；旧队列若没有任何原收件人记录则取消，不转发给新名单。正常邮箱格式和授权码检查仍保留。

`test-email` 会向全部配置的收件邮箱发送测试邮件；只要任一邮箱失败，整体报告就不是“全部接受”。该测试入口不自动重试；手动再次执行会重新发送给所有目标邮箱。

`notifications.channel = "console"` 时仅打印到终端。要发邮件，需要将其改为 `smtp`，填写 host、port、username、to，并在同一 notifications 段填写 `QQ_SMTP_AUTH_CODE = "你的授权码"`（QQ SMTP 授权码，不是登录密码）。配置值优先；留空时读取同名环境变量。`config.browser.local.toml` 已被 .gitignore 排除，不要公开分享填好授权码的配置。切换通道后重启监控，无需更换 monitor.database。

默认只提醒 24 小时内、尚未处理过版本的重置预告、已重置、重置券、额度变化、更正或待核实线索。无关内容和普通周期恢复不提醒。发送队列会重试失败项；SMTP 接受不等于邮件一定进入收件箱。当前提前提醒关闭（reminder_minutes=0）。

桌面启动器的自测和浏览器预览不会发送测试邮件；需要核验邮箱收信时手动执行上面的 `test-email`。

## 验证与历史记录

离线演示：`python -m quota_monitor demo`。Python 测试：`python -m unittest discover -s tests -v`。浏览器 DOM 测试需要 Playwright。

本地采集验证不代表长期不漏报，也不等于已确认邮件进入收件箱。运行数据与内部排查记录不随源码发布。

代码中仍保留 API 模式，但当前没有为其保留配置；不要用普通 `run` 命令启动本次浏览器监控。
