# AI产品工厂

在一个对话工作台里，把想法推进到需求、方案、开发、检查和本机交付。

- [产品展示页](https://xiaohaxi2-source.github.io/ai-product-factory/)
- [下载完整源码](https://github.com/xiaohaxi2-source/ai-product-factory/archive/refs/heads/main.zip)

## 已有功能

集中对话、可选项及自填的需求澄清、PRD草稿与确认、技术方案/开发任务、受控代码生成、持久化任务与续跑、版本差异、Python/FastAPI检查与本机预览、工程验收与代码采用、文档预览/下载、源码ZIP、本机保存路径。顶部流程和左侧项目导航自动伸缩，用户消息显示在右侧。

GitHub Pages是产品介绍和界面截图导览。真实生成依赖本机Codex CLI、Python后端与数据库，需下载源码后在本机运行；Pages不提供在线生成。云部署、多人账号和通用前端工程构建仍未接入。

## 本机安装（当前验证环境为macOS）

准备Python 3.12、Node.js、Git，以及已安装并完成登录的Codex CLI。使用现有账号额度，不自动充值或切换API计费。Node.js用于JavaScript逻辑测试。

1. 下载或克隆本仓库并解压。
2. 在仓库根目录运行`bash install.sh`，或在macOS双击`install.command`。这会创建项目虚拟环境并安装requirements中的固定依赖。
3. 在macOS双击根目录`start.command`，或运行：

```bash
projects/AI产品工厂/backend/.venv/bin/python projects/AI产品工厂/scripts/server.py
```

4. 打开http://127.0.0.1:8124，新建自己的产品。关闭启动终端或按Control+C停止服务。

默认不需要API Key。可选配置参考`projects/AI产品工厂/backend/.env.example`；真实配置只保存为被Git忽略的backend/.env。不要提交密钥或个人数据。

`requirements.txt`固定直接依赖，`requirements-lock.txt`记录验证环境的完整依赖版本。安装脚本使用直接依赖；需要复现完整环境时可将pip的-r参数改为requirements-lock.txt。

## 验证

```bash
cd projects/AI产品工厂
backend/.venv/bin/python -m pytest backend/tests -q
```

公开发布前在独立源码目录复核自动测试。测试使用隔离临时目录和模型替身，不消耗账号额度。scripts/smoke*.py是真实生成回归，显式运行会使用当前账号额度；不附带原开发者的调用日志或个人数据。

## 目录

```text
site/                       GitHub Pages静态展示页，入口index.html
projects/AI产品工厂/
  backend/app/              后端、任务、开发/检查与交付逻辑
  backend/static/           完整本机网页
  backend/alembic/           数据库迁移
  backend/tests/             自动化测试
  scripts/                  启动、重启和真实回归脚本
  docs/                     公开需求和架构说明
docs/手册/                  idea-to-launch工具包手册及参考代码
AGENTS.md / kit.sh / verify.sh  共用规则与流程管家
```

首次启动会在项目目录创建data/。会话和确认保存在本机数据库；文档位于data/deliverables，代码版本位于data/executions，检查报告位于data/verifications。新产品的文档/采用代码按确认写入projects/<产品名>。不自动删除或覆盖独立改动。

## Pages发布

`site/`包含完整静态展示文件。仓库gh-pages分支根目录发布相同静态内容，Pages发布源为gh-pages的/。展示页不依赖API，也不会连接或读取访客本机数据。

## 发布范围与许可

保留完整产品源码、测试、提示词、迁移、规则、必要手册、示例配置和静态资源。排除真实.env、账号凭据、聊天/数据库、用户生成项目、运行缓存、开发证据截图/日志和私人项目状态；展示截图只使用虚构验收样例。排除项不影响新安装运行，原本机数据未删除。

沿用原工具包[MIT License](LICENSE)，保留版权声明。上游工具包版权为Copyright (c) 2025 hubooooooo。此仓库为在工具包基础上实现的产品工厂。
