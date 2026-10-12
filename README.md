<p align="center">
  <img src="assets/quota-monitor.svg" width="80" alt="项目 Logo">
</p>

<h1 align="center">Codex 额度重置消息监控</h1>

<p align="center">把分散的重置消息，整理成可追溯的线索、桌面提醒与邮件。</p>

<p align="center"><strong>Windows 后台运行</strong> · 默认每 30 分钟检查 · 无需 X API Key</p>

<p align="center">
  <a href="https://github.com/AntoSmith/Tools-CodexQuotaResetMonitor/releases/latest">下载 Windows 便携版</a> ·
  <a href="#界面一览">界面一览</a> · <a href="#快速开始">快速开始</a> ·
  <a href="docs/USAGE.md">使用说明</a> · <a href="config.browser.example.toml">配置模板</a>
</p>

![六项功能：多源发现、对话上文、后台监控、个性化邮件、订阅管理、历史分类](assets/feature-overview.svg)

<details>
<summary><strong>功能详情 · 重置预告、已重置候选、重置券、额度变化、延期撤回与待核实线索</strong></summary>

| 功能 | 使用体验 |
| --- | --- |
| 🔎 多源发现 | 公开索引定位 Tibo 帖子与回复，后台浏览器读取 X；支持配置其他公开账号。 |
| 💬 上文辅助判断 | 结合可读取的对话上文筛选重置线索，含糊回应保留“待核实”说明。 |
| 🖥️ 桌面状态与提醒 | 托盘、自启动、轮次、采集结果与倒计时；新线索弹窗可直接人工标记。 |
| 🎨 历史分类 | 🟢确定 / 🟠模糊 / 🟣不确定 / ⚪未标记，保存原文与版本，支持筛选、改标和清除。 |
| 👥 本地订阅管理 | 中文备注、多邮箱、启停、起止时间、续期、待核实开关、单人测试邮件。 |
| 📬 个性化邮件 | 原文与来源、北京时间、接收偏好、卡通动图；支持即时、半小时汇总、每日汇总及夜间免打扰。 |

</details>

## 界面一览

<table>
  <tr>
    <td width="50%"><strong>监控面板</strong><br>轮次、采集状态、下一轮倒计时<br><br><img src="assets/screenshots/dashboard.png" alt="监控面板合成演示" width="100%"></td>
    <td width="50%"><strong>订阅管理</strong><br>每位收件人都有自己的期限和发送偏好<br><br><img src="assets/screenshots/subscribers.png" alt="订阅管理：虚构联系人及 example.com 邮箱" width="100%"></td>
  </tr>
  <tr>
    <td><strong>历史线索</strong><br>按颜色分类，回看原文与上下文<br><br><img src="assets/screenshots/history.png" alt="历史线索与四色人工标记：合成数据" width="100%"></td>
    <td><strong>新线索提醒</strong><br>查看来源，选择判断，留待以后回看<br><br><img src="assets/screenshots/alert.png" alt="新线索弹窗：合成示例及人工标记按钮" width="100%"></td>
  </tr>
</table>

<details>
  <summary><strong>📨 展开查看邮件样式与卡通动图</strong></summary>
  <p>邮件包含来源、上下文和个人接收偏好；支持 HTML 与纯文本。</p>
  <p align="center"><img src="assets/email/uncertain.gif" width="200" alt="待核实线索：疑惑动作"> <img src="assets/email/celebrate.gif" width="200" alt="作者声明已重置：庆祝动作"></p>
  <p align="center"><img src="assets/screenshots/email-demo.png" width="540" alt="合成邮件示例，非真实重置公告或收件记录"></p>
</details>

*截图均使用合成线索或虚构联系人；人工标记用于历史整理，独立于自动分类和邮件规则。*

## 一条线索的旅程

```mermaid
flowchart LR
    A[公开索引 / X 帖子] --> B[后台浏览器读取<br/>正文与可读上文]
    B --> C[规则分类 · 时效检查<br/>版本去重]
    C --> D[桌面提醒<br/>历史归档与人工标记]
    C --> E[订阅与发送偏好]
    E --> F[邮件投递<br/>按邮箱记录结果与重试]
    classDef source fill:#e8f5f1,stroke:#168176,color:#153b37
    classDef output fill:#edf1ff,stroke:#4761ac,color:#182c55
    class A,B source
    class D,F output
```

来源：[codex-reset.com](https://codex-reset.com/) 公开索引及 X 页面。第三方索引用于发现和定位；正文与可读取的上文由浏览器读取，缺失或未核实的关系保留说明。

## 快速开始

### 下载运行

**Windows 10/11 x64，无需自行安装 Python、Node.js 或 npm。**

1. 从 [GitHub Releases](https://github.com/AntoSmith/Tools-CodexQuotaResetMonitor/releases/latest) 下载 `windows-x64.zip`，完整解压到可写目录。
2. 双击 `CodexQuotaMonitor.exe`。首次启动从模板创建本机配置，并显示设置提示。
3. 在 **打开配置** 中核对监控账号、填写自己的邮件参数，配置后点击 **启动监控**。

便携版包含运行依赖，使用本机已有的 Chrome 或 Edge；解压后请保留整个文件夹。下载包只带模板，运行产生的配置、名单与数据保存在使用者本机。

<details>
<summary><strong>开发者：从源码构建</strong></summary>

**准备环境：** Windows · Python 3.11+ · Node.js/npm · Chrome 或 Edge

```powershell
git clone https://github.com/AntoSmith/Tools-CodexQuotaResetMonitor.git
cd Tools-CodexQuotaResetMonitor
Copy-Item config.browser.example.toml config.browser.local.toml
npm install
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-desktop.ps1
```

1. 编辑本地配置：核对监控账号，设为 `verified = true`；需要邮件时填写 SMTP 参数并切换到 `smtp`。
2. 双击 `CodexQuotaMonitor.exe`，在 **设置** 中管理订阅与自启动。
3. 在 **历史线索** 中回看记录；关闭主窗口收回托盘，托盘“退出”停止监控。

便携包构建：`python scripts/build-portable.py --version 0.2.0`。下载来源、校验与许可证见包内 `runtime-provenance.json` 和 `THIRD_PARTY_NOTICES.txt`。

</details>

[首次使用与 X 登录](docs/USAGE.md#首次使用) · [订阅管理](docs/USAGE.md#本地订阅管理) · [邮件测试](docs/USAGE.md#测试选中的收件邮箱) · [历史标记](docs/USAGE.md#历史线索与人工标记)

## 使用与隐私

| 你需要知道 | 说明 |
| --- | --- |
| 消息的含义 | 非官方候选线索，需结合原帖和账户状态判断；不提供充值或代重置。 |
| 运行条件 | 本机需联网且保持唤醒；页面限制和来源延迟会影响覆盖与及时性。 |
| 程序依赖 | 下载版自带 Python、Node.js 与 Playwright；源码版需准备依赖。两者都需保留完整文件夹。 |
| 私人数据 | 仓库只含配置模板；个人配置、收件名单、数据库、日志及浏览器会话留在本地。 |

[完整使用与排查](docs/USAGE.md) · [发布与隐私检查](docs/PUBLISH.md)
