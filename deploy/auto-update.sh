#!/bin/bash
# 科技内参 自动更新: 每5分钟检查 GitHub 远端新提交, 有则自动部署
# 由 root crontab 调用, 日志: /var/log/tech-neican-autoupdate.log

APP_DIR=/opt/tech-neican
LOG=/var/log/tech-neican-autoupdate.log

# 防止多次 cron 重叠执行
exec 9>/tmp/tech-neican-autoupdate.lock
flock -n 9 || exit 0

cd "$APP_DIR" || exit 1
git fetch origin main -q 2>>"$LOG" || exit 1

LOCAL=$(git -c safe.directory='*' rev-parse HEAD)
REMOTE=$(git -c safe.directory='*' rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
    echo "[$(date '+%F %T')] 发现新提交 ${REMOTE:0:7}, 开始自动部署..." >> "$LOG"
    flock -w 120 /tmp/tech-neican-deploy.lock bash "$APP_DIR/deploy/update.sh" >> "$LOG" 2>&1
    echo "[$(date '+%F %T')] 自动部署完成" >> "$LOG"
fi
