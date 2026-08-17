#!/usr/bin/env bash
# ============================================================
# 科技内参 - 腾讯云一键部署脚本 (Ubuntu/Debian/CentOS/OpenCloudOS 通用)
#
# 用法(任选其一):
#   A. 已 clone 仓库到服务器 (推荐, 配合 Git 更新链路):
#        git clone https://github.com/silentK-AI/Tech-Insider-Report.git
#        cd Tech-Insider-Report
#        sudo bash deploy/install.sh
#   B. scp 上传后部署:
#        scp server.py index.html deploy/install.sh root@<IP>:/tmp/
#        sudo bash /tmp/install.sh
#   C. Git 方式部署到 /opt/tech-neican:
#        sudo GIT_REPO=https://github.com/silentK-AI/Tech-Insider-Report.git bash install.sh
#
# 说明: 自动识别包管理器(apt/yum/dnf)与运行用户(www-data/nginx),
#       systemd 守护进程, 端口默认 8080 (可用 PORT 环境变量覆盖)
# ============================================================
set -e

APP_DIR=/opt/tech-neican
PORT=${PORT:-8080}
SRC=/tmp/tech-neican
GIT_REPO=${GIT_REPO:-}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> [1/5] 安装系统依赖 (python3 / nginx / git / curl)"
# 自动识别包管理器
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y python3 nginx git curl
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 nginx git curl
elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y python3 nginx git curl
else
    echo "!! 无法识别的包管理器, 请手动安装 python3 / nginx / git / curl"
    exit 1
fi

# 自动识别运行用户 (Debian/Ubuntu 是 www-data, CentOS/OpenCloudOS 是 nginx)
RUN_USER=""
if id www-data >/dev/null 2>&1; then
    RUN_USER=www-data
elif id nginx >/dev/null 2>&1; then
    RUN_USER=nginx
else
    sudo useradd -r -s /usr/sbin/nologin www-data
    RUN_USER=www-data
fi
echo "   运行用户: $RUN_USER"

echo "==> [2/5] 部署代码到 $APP_DIR"
sudo mkdir -p "$APP_DIR"
if [ -n "$GIT_REPO" ]; then
    # Git 方式: 直接克隆仓库 (仓库需含 server.py / index.html)
    if [ -d "$APP_DIR/.git" ]; then
        echo "   已存在 git 仓库, 执行 git pull"
        sudo -u "$RUN_USER" git -C "$APP_DIR" pull --ff-only
    else
        sudo git clone "$GIT_REPO" "$APP_DIR"
    fi
elif [ -d "$SRC" ]; then
    sudo cp -r "$SRC"/. "$APP_DIR"/
elif [ -f "$SCRIPT_DIR/server.py" ] && [ -f "$SCRIPT_DIR/index.html" ]; then
    # 场景 A: 已在 clone 的仓库里执行 deploy/install.sh
    echo "   从仓库目录 $SCRIPT_DIR 部署"
    sudo cp "$SCRIPT_DIR/server.py" "$SCRIPT_DIR/index.html" "$APP_DIR"/
    sudo mkdir -p "$APP_DIR/deploy"
    sudo cp -r "$SCRIPT_DIR/deploy"/. "$APP_DIR/deploy"/ 2>/dev/null || true
elif [ -f /tmp/server.py ] && [ -f /tmp/index.html ]; then
    sudo cp /tmp/server.py /tmp/index.html "$APP_DIR"/
else
    echo "!! 未找到代码文件。请选择: 仓库内执行 / scp 上传 / 设置 GIT_REPO"
    exit 1
fi
sudo chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"

echo "==> [3/5] 配置 systemd 服务"
sudo tee /etc/systemd/system/tech-neican.service > /dev/null <<EOF
[Unit]
Description=Tech Neican (科技内参) Market Proxy
Documentation=https://localhost
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
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
sudo mkdir -p /etc/nginx/conf.d
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
# 部分发行版(如 OpenCloudOS) 主配置可能未 include conf.d, 检测并提示
if ! grep -qE "conf\.d/.*\.conf" /etc/nginx/nginx.conf 2>/dev/null; then
    echo "!! 警告: /etc/nginx/nginx.conf 未 include conf.d/*.conf"
    echo "   请将 /etc/nginx/conf.d/tech-neican.conf 的内容合并进 nginx.conf 的 http{} 块"
    echo "   然后: nginx -t && systemctl reload nginx"
else
    sudo nginx -t && sudo systemctl reload nginx || echo "!! Nginx 配置失败, 请手动检查 nginx -t 输出"
fi

echo
echo "======================================================"
echo "✅ 部署完成"
echo "  服务状态 : systemctl status tech-neican"
echo "  实时日志 : journalctl -u tech-neican -f"
echo "  本机验证 : curl http://127.0.0.1:$PORT/api/overview"
echo "  对外访问 : http://<服务器公网IP>/   (80端口, 需云防火墙放行)"
echo "  重启服务 : sudo systemctl restart tech-neican"
echo "  更新代码 : sudo bash $APP_DIR/deploy/update.sh"
echo "======================================================"
