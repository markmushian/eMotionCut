# eMotionCut 安装与验收

先完整读 README.md，按其中安装步骤依次执行。仓库是公开的，不需要 GitHub 登录。不要读取用户的密码、令牌或私钥；智能体登录与 API 密钥输入由用户自行完成，不代读登录凭据。

首次安装运行 `npm run setup`：检查系统、Node 与磁盘，安装根目录和 vendor/openscreen 两份锁定依赖、匹配的 Playwright Chromium、独立 Python + numpy，并执行 build。随后运行 typecheck、test；用户在设置接通智能体后，运行 `npm run doctor`。任何一步非零退出都不能当作成功。可重复运行 setup，不用 sudo，不清空旧构建。`npm start` 打开原生窗口；验收实际首页与剪辑台，不把构建通过当作功能验收。

OpenScreen 可在应用内一键下载安装：必须校验 `WINDOWS-RUNTIME-LOCK.json` 里的 installerSha256，校验不过一律不执行；GitHub 加速镜像只当传输通道，内容以哈希为准。Ego Lite 是默认浏览器（发布、抓作品、MotionGit 授权都用它），缺失时退 Chrome / Edge 并在界面上说明。不自动授权屏幕录制；需要权限时请用户在系统界面操作。

## 更新

按 README「快速开始」重新执行安装与构建即可；已接通的智能体配置与密钥保留在本机钥匙串，不受更新影响。
