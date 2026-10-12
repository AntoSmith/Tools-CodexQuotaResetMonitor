# 手动发布到 GitHub

推荐使用已经准备好的 `github-export` 目录。里面只有源码、图标、测试、配置模板与说明；没有本地配置、运行数据、EXE 或开发过程记录。创建桌面 EXE 时按 README 在自己的电脑上构建。

普通使用者可直接从 [Releases](https://github.com/AntoSmith/Tools-CodexQuotaResetMonitor/releases/latest) 下载便携版。下面的 Git 步骤面向维护者，便携版使用者无需执行。

在 GitHub 创建一个空仓库，然后在 PowerShell 中进入 `github-export`，执行：

```powershell
git init
git branch -M main
git add .
git status --short
git diff --cached --name-only
# 确認上面只有预期发布的文件，再提交。
git commit -m "Initial source release"
git remote add origin https://github.com/YOUR_NAME/YOUR_REPO.git
git push -u origin main
```

将远程地址换成自己的仓库地址。本文不会自动创建仓库或推送。

### Windows 所有者检查

如果目录由 Codex 沙箱账户创建，自己的 Git Bash 提示 `detected dubious ownership`，确认目录是自己的项目后，在自己的终端执行以下命令，仅信任这个目录：

```bash
# 在待发布仓库目录中执行，仅信任当前目录。
git config --global --add safe.directory "$(pwd -W)"
```

这是本地 Git 的目录信任设置，不会改变提交者或 GitHub 登录账号。随后执行上面的 `git add`、`commit`、`push`。若缺少提交者身份，在仓库内用 `git config user.name "你的名字"` 和 `git config user.email "你在 GitHub 使用的邮箱"` 设置；推送时使用自己的 GitHub 账号完成凭据登录。无需重新 `git init`。文件名是 `README.md`，不是 `READNE.md`。已经存在 origin 时先执行 `git remote -v` 核对地址，不要重复添加。

## 可上传内容

- `quota_monitor/`、`desktop/`、`scripts/`、`tests/`：程序、构建工具和测试。
- `assets/`：项目图标、原创邮件卡通动图与使用虚构数据的演示截图。
- `config.browser.example.toml`：公开配置模板，邮箱为 example.com 示例、授权码留空。
- `.gitignore`、`README.md`、`package.json`、`docs/USAGE.md` 与本说明。

## 不上传内容

- `config*.toml`（仅放行 `config*.example.toml`）、`.env`、`.env.*`（仅放行 `.env.example`）。
- `data/`：数据库、邮件队列、界面保存的收件邮箱、浏览器登录数据、诊断文件和本机依赖路径。
- `logs/`、数据库文件、缓存、`node_modules/`、EXE、快捷方式及备份文件。
- 内部开发与现场排查记录（`docs/` 仅放行本说明与 `USAGE.md`）。

新使用者应把模板复制为 `config.browser.local.toml`，在这个忽略的文件里填写发件邮箱和授权码；收件邮箱可通过桌面设置保存到已忽略的 `data/`。不要把真实值填进 example 模板，也不要使用 `git add -f` 强制加入私有文件。

`.gitignore` 只对未跟踪文件生效，无法撤销已提交内容。如果误提交秘密，应撤销对应密钥/授权码并清理历史，不能只添加忽略规则。

`github-export` 是本次源码快照。后续开发建议以初始化后的发布目录作为仓库维护，避免从运行目录复制配置和数据。

## 便携版发布

在 Windows 构建环境中运行 `python scripts/build-portable.py --version 0.2.0`。脚本只从明确白名单组装全新目录，下载固定版本的官方 Python embeddable、Node.js 与 Playwright 包，并核对 SHA256 或 npm integrity；不会复制当前的本地配置、运行时路径标记、名单或浏览器资料。

产物为 `dist/CodexQuotaMonitor-v0.2.0-windows-x64.zip` 和 `dist/SHA256SUMS.txt`。ZIP 自带运行依赖、许可证及来源清单，只含配置模板；首次运行时生成使用者自己的本地配置。不要将测试后已产生数据的解压目录重新压缩发布，应发布构建脚本输出的原始干净 ZIP。

发布前应在移除系统 Python/Node.js PATH、含中文和空格的隔离目录验证导入、桌面启动、历史保存及浏览器解析，并逐项检查 ZIP 文件清单。公开仓库前还要检查 Git 可达历史中的配置、个人邮箱和本机路径，当前文件干净并不代表历史干净。
