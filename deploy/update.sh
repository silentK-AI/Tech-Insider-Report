#!/usr/bin/env bash
# ============================================================
# 科技内参 - 服务器一键更新脚本 (Git 方式)
#
# 前提: 服务器上的 /opt/tech-neican 是 git 仓库
#       (即 install.sh 用 GIT_REPO 方式部署的, 或手动 git init 过的)
#
# 用法: 本地改完代码 commit+push 后, SSH 登录服务器执行:
#       sudo bash /opt/tech-neican/deploy/update.sh
#   或简写: bash /tmp/update.sh
# ============================================================
set -e

APP_DIR=/opt/tech-neican

if [ ! -d "$APP_DIR/.git" ]; then
    echo "!! $APP_DIR 不是 git 仓库, 无法 git 更新"
    echo "   请改用: sudo cp /tmp/server.py /tmp/index.html $APP_DIR/ && sudo systemctl restart tech-neican"
    exit 1
fi

echo "==> [1/3] 拉取最新代码"
cd "$APP_DIR"
sudo -u www-data git pull --ff-only

echo "==> [2/3] 重启服务"
sudo systemctl restart tech-neican

echo "==> [3/3] 自检"
sleep 2
curl -s -o /dev/null -w "  GET /api/overview -> HTTP %{http_code}\n" \
     --max-time 10 "http://127.0.0.1:8080/api/overview" || true
echo "✅ 更新完成 (当前版本: $(cd "$APP_DIR" && git log --oneline -1))"
