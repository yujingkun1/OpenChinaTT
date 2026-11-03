# 部署与维护

本项目使用单机SQLite，适合以一个网站进程和一个结算任务服务同一学校/俱乐部。数据库放在代码目录之外，更新代码不会覆盖用户资料和赛果。

## systemd + Nginx

以下假设代码位于 `/opt/openchinatt/current`、虚拟环境位于 `/opt/openchinatt/venv`、数据位于 `/var/lib/openchinatt`。可按实际情况修改所有示例中的路径及端口。

```sh
sudo useradd --system --user-group --home-dir /var/lib/openchinatt --shell /usr/sbin/nologin openchinatt
sudo install -d -m 0700 -o openchinatt -g openchinatt /var/lib/openchinatt
sudo python3 -m venv /opt/openchinatt/venv
sudo /opt/openchinatt/venv/bin/python -m pip install -r /opt/openchinatt/current/requirements.txt
sudo install -m 0600 /opt/openchinatt/current/.env.example /etc/openchinatt.env
```

编辑 `/etc/openchinatt.env`，至少设置：

```dotenv
TT_ORIGIN=https://tt.example.edu
TT_DATA=/var/lib/openchinatt
TT_HOST=127.0.0.1
TT_PORT=8097
TT_BASE=/pingpong
TT_ADMIN_EMAIL=admin@example.edu
TT_ADMIN_NAME=管理员
```

首次创建管理员时，在加载同一配置的环境中以服务用户运行bootstrap；生成的初始密码/恢复码只显示在当前终端，不要发到公开日志或仓库。

```sh
cd /opt/openchinatt/current
sudo bash -c 'set -a; source /etc/openchinatt.env; set +a; runuser -u openchinatt -- /opt/openchinatt/venv/bin/python -m app.manage bootstrap'
sudo cp deploy/openchinatt.service /etc/systemd/system/
sudo cp deploy/openchinatt-settle.service deploy/openchinatt-settle.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openchinatt.service openchinatt-settle.timer
```

将 `deploy/nginx-location.conf` 放入自己的HTTPS虚拟主机配置，校验后重载Nginx。它只占用 `/pingpong/` 路径。若另选前缀，须同步修改 `TT_BASE`、Nginx路径和访问URL。域名与证书由各机构自行配置，数据库和配置文件不要放进Nginx静态目录。

## 备份和恢复

在线备份使用SQLite备份API，不直接复制正在使用的数据库文件：

```sh
cd /opt/openchinatt/current
# 在已加载生产配置、可写备份目录的环境运行
/opt/openchinatt/venv/bin/python -m app.manage backup --output /secure-backups/club.sqlite3
```

头像也在数据库内，无需另备份公开上传目录。可按自己的保留策略添加每日备份任务。

恢复前停止网站及结算任务，将备份放回 `TT_DATA/club.sqlite3`，保证服务用户有读写权限，再启动。先保留当前数据库副本，不要把备份或账号恢复码提交到GitHub。

## 更新

1. 记录当前版本并在线备份数据库。
2. 在新目录拉取代码、安装依赖、运行测试；必要时在备份副本上验证迁移。
3. 切换代码目录或symlink，仅重启本站服务。
4. 访问 `/pingpong/api/ready`，检查现有账号/战绩和定时器。

迁移只增加字段与表，保留现有数据。日结仅处理已过午夜截止时间的已确认单打；重复执行不会重复计分。补执行可以运行 `python -m app.manage settle`，它仍遵循午夜截止时间，不会提前结算今天刚确认的比赛。

## 诊断

```sh
systemctl status openchinatt.service openchinatt-settle.timer
systemctl list-timers openchinatt-settle.timer
journalctl -u openchinatt.service -n 50 --no-pager
journalctl -u openchinatt-settle.service -n 50 --no-pager
```

Docker部署查看 `docker compose logs web settler`。修改域名时须同步修改 `TT_ORIGIN`，否则写入请求会被同源校验拒绝；这是登录/保存资料失败时首先需要检查的配置。
