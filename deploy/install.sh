#!/usr/bin/env bash
# ============================================================
# 科技内参 - 腾讯云一键部署脚本 (Ubuntu 20.04+ / Debian 11+)
#
# 用法:
#   1. 先上传代码到服务器(任选其一):
#      scp -r /Users/jiangpei/WorkBuddy/科技内参/server.py \
#              /Users/jiangpei/WorkBuddy/科技内参/index.html \
#              /Users/jiangpei/WorkBuddy/科技内参/deploy/install.sh \
#              ubuntu@<服务器IP>:/tmp/
#   2. SSH 登录服务器后执行:
#      sudo bash /tmp/install.sh
#
# 说明: 脚本自动安装 python3 + nginx, 配置 systemd 守护进程,
#       端口默认 8080 (可用环境变量 PORT 覆盖, 如 PORT=8000 bash install.sh)
# ============================================================
set -e

APP_DIR=/opt/tech-neican
PORT=${PORT:-8080}
SRC=/tmp/tech-neican

echo "==> [1/5] 安装系统依赖 (python3 / nginx)"
sudo apt-get update -y
sudo apt-get install -y python3 nginx curl

echo "==> [2/5] 部署代码到 $APP_DIR"
sudo mkdir -p "$APP_DIR"
# 支持两种来源: /tmp/tech-neican/ 目录 或 /tmp/ 下的散文件
if [ -d "$SRC" ]; then
    sudo cp -r "$SRC"/. "$APP_DIR"/
elif [ -f /tmp/server.py ] && [ -f /tmp/index.html ]; then
    sudo cp /tmp/server.py /tmp/index.html "$APP_DIR"/
else
    echo "!! 未找到代码文件。请先上传 server.py / index.html 到 /tmp/ 或 /tmp/tech-neican/"
    exit 1
fi
sudo chown -R www-data:www-data "$APP_DIR"

echo "==> [3/5] 配置 systemd 服务"
sudo tee /etc/systemd/system/tech-neican.service > /dev/null <<EOF
[Unit]
Description=Tech Neican (科技内参) Market Proxy
Documentation=https://localhost
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 $APP_DIR/server.py $PORT
Restart=always
RestartSec=3
# 如需不经 Nginx 直接对外访问, 取消下行注释 (并放行 $PORT 端口)
# Environment=HOST=0.0.0.0

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now tech-neican
sleep 3

echo "==> [4/5] 本机自检"
curl -s -o /dev/null -w "  GET /api/overview    -> HTTP %{http_code}\n" \
     --max-time 10 "http://127.0.0.1:$PORT/api/overview" || true
curl -s -o /dev/null -w "  GET /                -> HTTP %{http_code}\n" \
     --max-time 10 "http://127.0.0.1:$PORT/" || true

echo "==> [5/5] 配置 Nginx 反向代理 (HTTP)"
sudo tee /etc/nginx/conf.d/tech-neican.conf > /dev/null <<EOF
server {
    listen 80;
    server_name _;   # 有域名后改成你的域名, 如 tech.example.com

    gzip on;
    gzip_types text/plain text/css application/json application/javascript;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
    }
}
EOF
sudo nginx -t && sudo systemctl reload nginx || echo "!! Nginx 配置失败, 请手动检查 /etc/nginx/conf.d/tech-neican.conf"

echo
echo "======================================================"
echo "✅ 部署完成"
echo "  服务状态 : systemctl status tech-neican"
echo "  实时日志 : journalctl -u tech-neican -f"
echo "  本机验证 : curl http://127.0.0.1:$PORT/api/overview"
echo "  对外访问 : http://<服务器公网IP>/   (80端口, 需云防火墙放行)"
echo "  重启服务 : sudo systemctl restart tech-neican"
echo "======================================================"
