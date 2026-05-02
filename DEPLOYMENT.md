# Abao服务器部署指南

这份文档面向一台 Ubuntu 服务器上的个人实例部署。默认流程使用 `IP + 端口` 直接访问，跑通到网页可聊天就算完成。Nginx 和 HTTPS 放在后面的可选高级流程。

> 约定：示例安装目录是 `/opt/abao`，服务端口是 `8020`，服务器公网 IP 用 `你的服务器IP` 表示。

## 1. 准备服务器

登录服务器：

```bash
ssh ubuntu@你的服务器IP
```

更新系统并安装基础依赖：

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3 python3-venv python3-pip
```

如果系统提示 `*** System restart required ***`，可以找一个空档重启：

```bash
sudo reboot
```

## 2. 从 GitHub 下载代码

创建安装目录：

```bash
sudo mkdir -p /opt/abao
sudo chown -R $USER:$USER /opt/abao
```

从 GitHub 克隆项目：

```bash
git clone https://github.com/xi-pengfei/abao.git
cd /opt/abao
```

如果你还没有把项目上传到 GitHub，先在本地完成上传；服务器部署时尽量从 GitHub 拉取，而不是从电脑手动复制。这样后面升级会简单很多。

## 3. 创建 Python 虚拟环境

Ubuntu 默认可能没有 `python` 命令，所以先用 `python3` 创建 venv：

```bash
cd /opt/abao
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

确认当前 Python 来自虚拟环境：

```bash
which python
```

应看到：

```text
/opt/abao/venv/bin/python
```

## 4. 配置 `.env`

复制示例配置：

```bash
cd /opt/abao
cp .env.example .env
nano .env
```

至少填写你的模型密钥。常用项如下：

```env
DEEPSEEK_API_KEY=你的DeepSeekKey
DASHSCOPE_API_KEY=你的DashScopeKey

ABAO_OWNER_TOKEN=你自己设置的一串长token
```

`ABAO_OWNER_TOKEN` 用来保护手机网页接口。网页设置里的 `Access Token` 填这个值本身，不要加 `Bearer`，前端会自动加。

如果 `.env` 没有配置 `ABAO_OWNER_TOKEN`，后端不会鉴权，任何能访问这个地址的人都可以调用你的接口。不建议公网裸奔。

## 5. 手动启动测试

先用命令直接跑起来：

```bash
cd /opt/abao
source venv/bin/activate
python -m uvicorn server.app:app --host 0.0.0.0 --port 8020
```

另开一个 SSH 窗口，在服务器上测试：

```bash
curl -s http://127.0.0.1:8020/api/health
```

正常返回类似：

```json
{"ok":true,"name":"abao","display_name":"阿宝"}
```

如果配置了 `ABAO_OWNER_TOKEN`，可以继续测试鉴权：

```bash
curl -i http://127.0.0.1:8020/api/history
curl -i http://127.0.0.1:8020/api/history -H "Authorization: Bearer 你的token"
```

第一条应返回 `401`，第二条应返回历史消息。

测试完成后，在运行 uvicorn 的窗口按 `Ctrl+C` 停掉。否则后面 systemd 会因为端口占用启动失败。

## 6. 配置 systemd 常驻运行

创建服务文件：

```bash
sudo nano /etc/systemd/system/abao.service
```

写入：

```ini
[Unit]
Description=Abao FastAPI Service
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/abao
ExecStart=/opt/abao/venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 8020
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

如果你的服务器用户名不是 `ubuntu`，把 `User=ubuntu` 改成实际用户名。

启动并设置开机自启：

```bash
sudo systemctl daemon-reload
sudo systemctl enable abao
sudo systemctl start abao
sudo systemctl status abao
```

看到下面这一行就说明常驻成功：

```text
Active: active (running)
```

如果失败，先看日志：

```bash
sudo journalctl -u abao -n 100 --no-pager
```

如果日志里出现 `address already in use`，说明 8020 被旧进程占用：

```bash
sudo lsof -i :8020
sudo kill 旧进程PID
sudo systemctl restart abao
```

## 7. 放行端口并访问

确认服务监听在公网地址：

```bash
ss -lntp | grep 8020
```

应看到类似：

```text
0.0.0.0:8020
```

如果服务器启用了 UFW：

```bash
sudo ufw status
sudo ufw allow 8020/tcp
```

还需要在腾讯云控制台的安全组里放行 `8020` TCP。

浏览器打开：

```text
http://你的服务器IP:8020
```

进入页面右上角设置：

```text
Server URL: http://你的服务器IP:8020
Access Token: .env 里的 ABAO_OWNER_TOKEN
```

保存后发一句话测试。到这里，`IP + 端口` 部署已经完成。

## 8. 常用运维命令

查看状态：

```bash
sudo systemctl status abao
```

重启：

```bash
sudo systemctl restart abao
```

看日志：

```bash
sudo journalctl -u abao -f
```

停止：

```bash
sudo systemctl stop abao
```

清理失败计数：

```bash
sudo systemctl reset-failed abao
```

## 9. 可选：Nginx + HTTPS

如果你只是自己临时使用，`IP + 8020` 可以先跑。但如果要长期放在手机主屏，HTTPS 更稳，PWA 能力也更完整。

安装 Nginx 和 Certbot：

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

正式走 Nginx 时，建议把 uvicorn 改回只监听本机：

```ini
ExecStart=/opt/abao/venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port 8020
```

修改后重启：

```bash
sudo systemctl daemon-reload
sudo systemctl restart abao
```

创建 Nginx 配置：

```bash
sudo nano /etc/nginx/sites-available/abao
```

示例：

```nginx
server {
    listen 80;
    server_name 你的域名;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8020;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
    }
}
```

启用配置：

```bash
sudo ln -s /etc/nginx/sites-available/abao /etc/nginx/sites-enabled/abao
sudo nginx -t
sudo systemctl reload nginx
```

申请 HTTPS 证书：

```bash
sudo certbot --nginx -d 你的域名
```

完成后访问：

```text
https://你的域名
```

腾讯云安全组需要放行 `80` 和 `443`。如果已经改用 Nginx，`8020` 不再需要对公网开放。

## 10. 同一台服务器部署多个阿宝

每个阿宝应该有独立目录、独立 `.env`、独立 `data/`、独立 systemd 服务和独立端口。

示例：

```text
/opt/abao-pengfei   -> 8020 -> abao-pengfei.service
/opt/abao-friend1   -> 8021 -> abao-friend1.service
/opt/abao-test      -> 8022 -> abao-test.service
```

部署第二个实例：

```bash
sudo mkdir -p /opt/abao-friend1
sudo chown -R $USER:$USER /opt/abao-friend1
git clone https://github.com/你的用户名/你的仓库名.git /opt/abao-friend1
cd /opt/abao-friend1
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
```

第二个实例的 `.env` 应该使用不同的 token，也可以使用不同模型 key：

```env
ABAO_OWNER_TOKEN=另一个长token
```

创建第二个服务：

```bash
sudo nano /etc/systemd/system/abao-friend1.service
```

示例：

```ini
[Unit]
Description=Abao Friend1 FastAPI Service
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/abao-friend1
ExecStart=/opt/abao-friend1/venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 8021
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable abao-friend1
sudo systemctl start abao-friend1
```

如果走 Nginx，可以给不同实例配置不同域名或路径。最清晰的是不同域名：

```text
pengfei.example.com -> 127.0.0.1:8020
friend1.example.com -> 127.0.0.1:8021
```

不要让多个实例共用同一个 `data/` 目录，否则记忆、性格和日记会混在一起。

## 11. 从 GitHub 升级

运行时最重要的数据在：

```text
.env
data/
config/birth_traits.yaml
config/providers.yaml
```

其中 `.env` 和 `data/` 不应该提交到 GitHub，也不应该被升级覆盖。`config/` 里的文件如果你改过，也要小心合并。

升级前先停服务：

```bash
sudo systemctl stop abao
cd /opt/abao
```

备份关键数据：

```bash
cp -a .env .env.backup.$(date +%Y%m%d%H%M%S)
cp -a data data.backup.$(date +%Y%m%d%H%M%S)
cp -a config config.backup.$(date +%Y%m%d%H%M%S)
```

拉取代码：

```bash
git pull
```

更新依赖：

```bash
source venv/bin/activate
python -m pip install -r requirements.txt
```

如果新版本更新了 `.env.example`，手动对照补充 `.env`，不要直接覆盖：

```bash
diff -u .env.example .env
```

如果新版本更新了 `config/providers.yaml` 或 `config/birth_traits.yaml`，优先人工合并。你的个性、模型配置和 app 名称都可能在这里。

如果版本说明要求重建索引，可以执行：

```bash
python -m scripts.rebuild_facts
python -m scripts.rebuild_embeddings
```

最后启动：

```bash
sudo systemctl start abao
sudo systemctl status abao
```

验证：

```bash
curl -s http://127.0.0.1:8020/api/health
```

如果升级跨度很大，建议先克隆到一个新目录，用新的端口试跑：

```text
/opt/abao-next -> 8030
```

确认没问题后，再停旧服务、复制或迁移 `data/`。这样可以最大限度避免把现有记忆弄坏。

## 12. 不要覆盖的东西

部署和升级时尤其注意：

- 不要覆盖 `.env`：里面有真实 API key 和 owner token。
- 不要删除 `data/`：里面是记忆库、性格状态和成长日记。
- 不要随手重建 `data/memory.db`：除非你明确要清空记忆。
- 不要把多个实例指向同一个 `data/`。
- 不要把真实 `.env` 上传到 GitHub。

如果不确定，先备份，再操作。
