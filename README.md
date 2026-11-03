<div align="center">
  <img src="public/logo.svg" width="88" alt="OpenChinaTT logo" />
  <h1>OpenChinaTT</h1>
  <p><strong>西南交通大学利兹学院自己的开球网</strong></p>
  <p>以球会友，记录每一场进步。其他学校、学院、机构和俱乐部也能自行部署使用。</p>
  <p>Self-hosted table tennis ratings, matchmaking, doubles and tournaments for schools and clubs.</p>
  <p><a href="LICENSE">MIT License</a> · Python 3.12+ · SQLite · Mobile first · Docker / Linux VPS</p>
</div>

OpenChinaTT 起源于西南交通大学利兹学院的乒乓球社区需求：让大家自由注册、约球、确认赛果、查看积分和战绩，也让管理员能组织周赛与选拔赛。

本项目是独立开发的开源校园平台。单打积分换算参考开球网公开 ChinaTT 积分表；它不连接、不同步开球网账号或官方积分。其他机构可直接部署本项目，修改学校名称、管理员和视觉标识，建立自己的乒乓球社区。

## 能做什么

| 模块 | 功能 |
| --- | --- |
| 账号 | 开放注册、密码登录、账号恢复码、修改密码、管理员权限隔离；可选SMTP邮箱验证码登录 |
| 个人档案 | 上传头像、球拍底板/正反手胶皮、左右手与握拍方式、自我介绍；自定义称号最多10个字 |
| 单打约战 | 自由发起邀请、对方同意、逐局报分、另一方确认；允许提前报分及同时约多场 |
| 积分 | 所有新用户1500分起步；北京时间每天00:00统一结算；积分榜前三显示金银铜牌 |
| 双打 | 四位不同球友组队，四人同意对战、四人确认比分；只统计战绩，不改变个人积分 |
| 统计榜 | 场次达人、福星/苦主、最近14天涨跌、双打固定组合胜率、个人双打胜率与最佳搭档 |
| 赛事 | 管理员创建周赛/选拔赛/公开赛，球友报名、设置种子、自动分组、小组循环、晋级淘汰赛、轮空与自动推进、冠军归档 |
| 管理 | 账号停用、赛果作废、赛事取消、操作审计、夜间积分重算 |
| 部署 | Python + SQLite，无前端构建步骤；Docker Compose或systemd + Nginx；在线数据库备份 |

手机端采用固定底部导航，中间“一键约球”，主要操作适合拇指完成；桌面端也有完整布局。

## 快速启动：Python

需要 Python 3.12+。前端为原生 JavaScript/CSS，不需要npm安装或打包。

```sh
git clone https://github.com/yujingkun1/OpenChinaTT.git
cd OpenChinaTT
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，先设置管理员邮箱和名称。加载环境后运行：

```sh
set -a
. ./.env
set +a
.venv/bin/python -m app.manage bootstrap
.venv/bin/python -m app.server
```

打开 **http://127.0.0.1:8097/pingpong/** 。`bootstrap` 只在没有管理员时创建账号，并在当前终端输出随机初始密码和恢复码，请私密保存。注册的新账号始终是普通用户，不能自行获得管理权限。

另开终端，同样加载 `.env` 后启动每日结算工作进程：

```sh
.venv/bin/python -m app.scheduler
```

工作进程按北京时间午夜执行，启动时也会补处理已过结算日的记录。Linux正式环境可以改用下方的systemd定时器。**只启动网站进程不会自动结算积分。**

## Docker Compose

```sh
cp .env.example .env
# 编辑 .env：设置管理员，以及用户浏览器实际访问的 TT_ORIGIN
# 本机测试可保留 TT_ORIGIN=http://127.0.0.1:8097
docker compose up -d --build
docker compose exec web python -m app.manage bootstrap
```

Compose包含网站和零点结算两个服务，共享持久化SQLite数据卷。默认端口映射仅为 `127.0.0.1:8097`；正式公网访问请通过Nginx或其他HTTPS反向代理。

```sh
docker compose logs --tail=50 web settler
docker compose exec web python -m app.manage backup --output /data/backup.sqlite3
docker compose cp web:/data/backup.sqlite3 ./backup.sqlite3
```

不要使用 `docker compose down -v` 进行普通更新，它会移除持久化数据卷。更新代码后重新运行 `docker compose up -d --build` 即可保留数据。

## 为你的学校或机构配置

| 环境变量 | 说明 | 示例 |
| --- | --- | --- |
| `TT_CLUB_NAME` | 导航中的短名称 | `某某乒乓` |
| `TT_CLUB_FULL_NAME` | 学校、学院、机构或俱乐部全称 | `某某大学乒乓球协会` |
| `TT_CLUB_ENGLISH` | 英文标识 | `CAMPUS TABLE TENNIS CLUB` |
| `TT_ADMIN_NAME` | 首次创建的管理员昵称 | `管理员` |
| `TT_ADMIN_EMAIL` | 保留给管理员的登录邮箱 | `admin@example.edu` |
| `TT_ORIGIN` | 浏览器实际访问的网站来源，**不包含路径** | `https://tt.example.edu` |
| `TT_BASE` | 访问路径前缀 | `/pingpong` |
| `TT_HOST` / `TT_PORT` | 后端监听地址/端口 | `127.0.0.1` / `8097` |
| `TT_DATA` | 独立数据目录 | `/var/lib/openchinatt` |

更换标志可替换 `public/logo.svg` 与 `public/favicon.svg`，配色在 `public/style.css` 顶部CSS变量中调整。学校名称不需要修改业务逻辑。

`TT_ADMIN_NAME` 和 `TT_ADMIN_EMAIL` 用于首次bootstrap，修改它们不会重命名或重置已有管理员。

## Linux VPS部署

示例文件在 [`deploy/`](deploy/)：

- `openchinatt.service`：常驻网站进程。
- `openchinatt-settle.service` 和 `.timer`：北京时间00:00积分结算，支持停机后补执行。
- `nginx-location.conf`：仅代理 `/pingpong/`，可与服务器上的其他站点并存。

完整路径及命令见 [部署指南](docs/deployment.md)。示例均使用占位地址，不包含任何实际服务器凭据。HTTPS证书由部署者自己的域名或IP入口配置。

## 积分与赛果规则

所有新账号统一 **1500** 分。单打由一方提交每局比分，另一方确认；提交者不能确认自己的赛果。确认后立即记入胜负战绩，个人积分进入待结算队列，**每天北京时间00:00** 按确认时间及比赛ID顺序统一更新。

| 结算时双方积分差 | 高分者胜：得失分 | 低分者胜：得失分 |
| --- | ---: | ---: |
| 0–12 | ±8 | ±8 |
| 13–37 | ±7 | ±10 |
| 38–62 | ±6 | ±13 |
| 63–87 | ±5 | ±16 |
| 88–112 | ±4 | ±20 |
| 113–137 | ±3 | ±25 |
| 138–162 | ±2 | ±30 |
| 163–187 | ±2 | ±35 |
| 188–212 | ±1 | ±40 |
| 213–237 | ±1 | ±45 |
| ≥238 | 0 | ±50 |

区间含边界，胜者加分等于负者扣分。双方1500时胜者1508、负者1492；同一场3:0和3:2的积分变化相同。换算参考[公开ChinaTT积分制度说明](https://kaiqiuwang.cc/home/space-24-do-blog-id-1124.html)，采用独立校内初始分，不应用官方首次定级、年度分或其他人工修正机制。

- 单打、双打均支持三局两胜、五局三胜、七局四胜，每局11分，10平后领先2分；未进行的局留空。
- 预约时间用于安排比赛，不限制提前报分；可以同时存在多场约战。
- 双打须四人同意，报分者视为确认，其他三人确认后完成。任一人退回赛果，会清除已确认状态，允许重新报分。
- 管理员作废赛果后保留原始记录，相关个人积分也在00:00统一重算。重算不提前纳入当天未到结算时点的比赛。
- 结算使用事务、赛前快照和积分流水，重复确认及重复运行结算命令不会重复加减分。

## 赛事怎么运行

1. 管理员创建赛事，选择类型、地点、赛制、小组数和默认晋级人数。
2. 球友自行报名。报名即同意该赛事生成的对战安排，比赛仍须双方确认赛果。
3. 管理员为已报名球友设置种子。种子按蛇形分配，非种子随机分组，各组人数差不超过1人，每组至少2人。
4. 自动生成组内单循环。排名依次比较：胜场、同胜场参赛者间胜场、净胜局、净得分、种子/报名顺序。
5. 小组赛全部确认后，管理员设置每组前几名晋级淘汰赛。
6. 淘汰赛自动处理轮空并在赛果确认后推进胜者，直到决出冠军；赛程、比分和冠军完整保留。

赛事模块目前为**单打小组赛 + 单败淘汰**。双打通过双打约战模块管理。进入晋级链的赛果禁止单独作废；管理员可以填写原因取消整场赛事，避免破坏后续对阵。

## 排行榜口径

- **场次榜**：已确认且未作废的单打、双打总场次，同时显示分项。
- **福星榜**：球友对某位对手胜率最高的对位，须至少有1场胜利。
- **苦主榜**：胜率最低的对位，须至少有1场失利。
- **双打组合榜**：固定两人的组合胜率；搭档顺序不影响组合归属。
- **双打个人榜**：球员全部双打的胜率，并显示最佳搭档及该搭档组合的样本数。
- **近两周涨跌**：最近14天已结算单打的积分净变化，按结算时间统计，双打不计入。

胜率至少1场即可上榜，始终展示胜场/总场次；同胜率优先场次更多者，方便判断样本量。

## 邮箱验证码与个人资料

密码注册、密码登录、恢复码重设默认可用。邮箱验证码须先配置SMTP，未配置时页面明确提示并禁用发信。

```dotenv
TT_SMTP_HOST=smtp.example.com
TT_SMTP_PORT=587
TT_SMTP_USER=sender@example.com
TT_SMTP_PASSWORD=your-app-password
TT_SMTP_FROM=sender@example.com
TT_SMTP_SSL=0
```

587使用STARTTLS；465设 `TT_SMTP_SSL=1`。验证码10分钟有效、60秒重发间隔、最多5次尝试、成功后立即失效。当前接口支持SMTP用户名/授权码认证；要求OAuth2的邮箱服务需要另行接入OAuth，不能只填写普通邮箱密码。

头像支持JPG/PNG/WebP，浏览器先居中裁剪压缩，服务器再次验证并重编码256×256 JPEG、移除EXIF；图片与数据库一起备份。称号最多10个Unicode字符，留空可清除，且只能修改自己的称号。称号是展示资料，不授予账号权限。

昵称、称号、头像、装备、已确认战绩为公开资料。普通约战的邀请/未确认赛果仅向参赛者展示；赛事赛程公开。邮箱仅本人和管理员可见，密码、会话令牌与恢复码使用哈希保存。

## 结构与验证

```text
app/        Python服务、账号、头像、单打/双打、统计、赛事、结算与迁移
public/     无构建前端、响应式样式、原创SVG标志
 tests/     权限、并发、积分、迁移、头像、称号、双打与完整赛事测试
 deploy/    通用Nginx与systemd示例
 docs/      部署与维护文档
```

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check public/app.js
```

当前包含29项后端测试，覆盖积分边界、并发防重复、访问控制、逐局校验、提前报分、同时多场、头像验证与迁移、称号10字边界、四人双打确认、午夜边界、作废重算、小组/淘汰完整流程和轮空。

## 贡献与复用

欢迎其他学校、机构和俱乐部部署、改名、修改和继续开发。问题与功能建议可以通过本仓库Issues提出；代码贡献欢迎Pull Request。提交前请运行上述检查，不要上传真实用户数据库、个人邮箱凭据、服务器密钥或私有部署记录。

项目使用 [MIT许可证](LICENSE)。本项目名称与说明用于介绍开源社区用途；部署者可按自己的学校或机构修改名称与标识。
