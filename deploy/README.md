# 科技内参 · 腾讯云部署指南

科技内参是**纯 Python 标准库**实现的动态服务（无任何第三方依赖），静态页与行情/资讯 API 由同一进程提供，部署极其简单。

- 代码：`server.py`（约 1000 行，行情代理 + RSS 聚合 + 自动翻译 + 去重）
- 页面：`index.html`（前端单页）
- 依赖：仅需 Python 3.8+，无需 pip install
- 对外形态：`python3 server.py [端口]`，默认监听 `127.0.0.1:8080`

---

## 一、方案选择

| 方案 | 适合场景 | 月成本参考 | 说明 |
|---|---|---|---|
| **轻量应用服务器** ⭐推荐 | 个人/小团队 | ¥50~100（2核2G） | 自带防火墙，最省心 |
| CVM 云服务器 | 需要弹性扩容/快照 | 按量或包年 | 更灵活 |
| CloudBase 云托管 | 不想管服务器 | 按量计费 | 需容器化改造，本包不覆盖 |

> 建议：**轻量应用服务器 2核2G 起步**，选 **Ubuntu 22.04/24.04** 或 **Debian 12** 镜像即可。本服务很轻（单进程 + 缓存），2G 内存绰绰有余。

---

## 二、购买与初始化（约 5 分钟）

1. 腾讯云控制台 → 轻量应用服务器 → 新建
2. 地域选**靠近你或目标读者**的（如广州/上海/北京；国内地域**直连海外 RSS 源无问题**，代码已做超时降级）
3. 镜像选 **Ubuntu 22.04 LTS**
4. 重置密码后，在控制台**防火墙**放行端口：`80`（HTTP）、`443`（HTTPS）、`22`（SSH，默认已开）
   - 如果不想用 Nginx，也可以放行 `8080` 后改 `HOST=0.0.0.0` 直接访问 `IP:8080`
5. 拿到公网 IP，本地终端测试连接：`ssh ubuntu@<IP>`

---

## 三、上传代码

> 💡 **推荐用 Git 方式部署**（文末《九、Git 版本管理》），以后每次更新只需服务器上 `git pull`，不用再 scp。以下 scp 方式适合只想快速跑起来的情况。

在**你本地电脑**执行（把 `<IP>` 换成服务器公网 IP）：

```bash
scp /Users/jiangpei/WorkBuddy/科技内参/server.py \
    /Users/jiangpei/WorkBuddy/科技内参/index.html \
    /Users/jiangpei/WorkBuddy/科技内参/deploy/install.sh \
    ubuntu@<IP>:/tmp/
```

---

## 四、一键部署（约 1 分钟）

SSH 登录服务器后：

```bash
sudo bash /tmp/install.sh
```

脚本会自动完成：安装 python3 + nginx → 部署代码到 `/opt/tech-neican` → 配置 systemd 守护（崩溃自动拉起、开机自启）→ 本机自检 → 配置 Nginx 反代。

验证：

```bash
curl http://127.0.0.1:8080/api/overview     # 本机 API 自检
curl -I http://<服务器IP>/                   # 通过 Nginx 访问首页
```

浏览器打开 `http://<服务器IP>/` 即可看到站点。

---

## 五、日常运维

```bash
systemctl status tech-neican        # 查看状态
journalctl -u tech-neican -f        # 实时日志
sudo systemctl restart tech-neican  # 重启服务
sudo systemctl stop tech-neican     # 停止
```

更新版本：本地 `git commit + push` → 服务器 `sudo bash /opt/tech-neican/deploy/update.sh`（git 方式）；或重新 `scp` 覆盖后 `sudo systemctl restart tech-neican`（非 git 方式）。

---

## 六、绑定域名 + HTTPS（可选）

> ⚠️ **重要**：域名解析到**中国大陆**的服务器必须完成 **ICP 备案**（腾讯云控制台有备案入口，约 1-2 周），否则域名访问会被拦截。未备案期间可直接用 `http://IP:8080` 访问。

已备案流程：
1. 修改 `/etc/nginx/conf.d/tech-neican.conf` 里的 `server_name` 为你的域名
2. `sudo nginx -t && sudo systemctl reload nginx`
3. 域名解析 A 记录指向服务器 IP（腾讯云 DNSPod）
4. 签发免费 HTTPS 证书：
   ```bash
   sudo apt install -y certbot python3-certbot-nginx
   sudo certbot --nginx -d tech.example.com   # 自动配置并加入自动续期
   ```

---

## 七、常见问题

| 问题 | 原因与解决 |
|---|---|
| 外网打不开，本机正常 | 腾讯云**防火墙未放行端口**（轻量控制台 → 防火墙 → 添加规则） |
| 页面能开、行情/资讯为空 | 服务器首次启动需预热约 30~60s（后台抓取+翻译），稍等刷新；`journalctl -u tech-neican` 看日志 |
| 个别海外源偶尔抓不到 | 上游源临时不可用，代码已做 TTL 缓存 + 失败降级，下轮自动恢复，不影响其他源 |
| 数据源被限流 | 翻译缓存 `.trans_cache.json` 会持久化到 `/opt/tech-neican/`，重启不丢 |
| 想换端口 | `PORT=9000 bash install.sh` 重新执行，或改 systemd 的 ExecStart |
| 日志刷太快 | 服务默认静默访问日志，只有错误才输出 |

---

## 八、数据源直连说明

服务运行期间需出网访问（国内服务器均可直连，代码均有超时与降级）：
- 东方财富行情/快讯、新浪美股行情、华尔街见闻 API（国内）
- 三星/SK海力士/OpenAI/英伟达/AMD 官方 RSS + CNBC（海外，国内直连可用）
- MyMemory 免费翻译 API（翻译缓存持久化，配额友好）

---

## 九、Git 版本管理（推荐）

本地代码已初始化为 git 仓库并完成首次提交（`.trans_cache.json`、`.workbuddy/` 等运行时文件已用 `.gitignore` 排除）。后续更新链路：**本地 commit+push → 服务器 git pull → 重启**，全程不用再传文件，还自带版本历史与回滚。

### 1. 推送代码到远端（只需一次）

在 Gitee（国内快，推荐）或 GitHub 新建一个**空仓库**（不要勾选初始化 README），然后本地执行：

```bash
cd /Users/jiangpei/WorkBuddy/科技内参

# Gitee 示例 (在 Gitee 网站创建空仓库后拿到地址)
git remote add origin https://gitee.com/你的用户名/tech-neican.git
git push -u origin main

# GitHub 示例
# git remote add origin https://github.com/你的用户名/tech-neican.git
# git push -u origin main
```

> 首次 push 会要求输入平台账号密码/令牌。Gitee 用账号密码即可；GitHub 现在需要用 **Personal Access Token**（Settings → Developer settings → Tokens）。

### 2. 服务器用 git 方式部署（一次性）

```bash
sudo bash /tmp/install.sh    # 改成先执行这个, 但上传时多传一个步骤也行
```

更直接的做法——SSH 登录服务器，用 git 部署（`install.sh` 已支持）：

```bash
# 上传脚本后, 带 GIT_REPO 执行
sudo GIT_REPO=https://gitee.com/你的用户名/tech-neican.git bash /tmp/install.sh
```

脚本检测到 `GIT_REPO` 就会 `git clone` 到 `/opt/tech-neican`（自动跳过 `.trans_cache.json` 等运行时文件），其余（systemd + Nginx）与 scp 方式完全一致。

### 3. 日常更新（以后每次改版只需 3 步）

```bash
# ① 本地改完代码后
cd /Users/jiangpei/WorkBuddy/科技内参
git add -A && git commit -m "本次改动说明"
git push

# ② SSH 登录服务器, 一条命令完成更新
sudo bash /opt/tech-neican/deploy/update.sh
```

`update.sh` 自动执行：`git pull`（拉新代码）→ `systemctl restart`（重启服务）→ 本机自检。要回滚旧版本：

```bash
cd /opt/tech-neican && sudo -u www-data git log --oneline -5   # 看历史版本
sudo -u www-data git checkout <版本号> -- server.py index.html # 回滚指定版本
sudo systemctl restart tech-neican
```

### 4. 两种方式对比

| | scp 直传 | **Git 管理** ⭐ |
|---|---|---|
| 首次部署 | 传 2 个文件 | `GIT_REPO=... install.sh` |
| 日常更新 | 重新 scp + 覆盖 + 重启 | `update.sh` 一条命令 |
| 版本历史/回滚 | ❌ 无 | ✅ `git log` + `checkout` |
| 误改恢复 | ❌ 无 | ✅ 一键还原 |
| 多设备协作 | ❌ 麻烦 | ✅ 随处 clone |
