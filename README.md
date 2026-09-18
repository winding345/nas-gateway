# nas-gateway

给家里 NAS 上的一堆自建服务套一层**带登录认证的反向代理**，让家人朋友用**手机浏览器**就能访问，
同时**局域网保持免登录**、现有的访问方式完全不受影响。

```
公网访客 ──HTTPS──▶ Tailscale Funnel ──▶ Caddy ──┬─ /authelia/*  → Authelia（登录页）
                                                  ├─ /personal-stock/* → 家库
                                                  ├─ /music/*         → 音乐
                                                  └─ /               → 黄页

局域网手机 ───────▶ 192.168.1.14:3456 ──▶ 家库          （直连，免登录）
```

## 特点

| | |
|---|---|
| 🔐 **统一登录** | 一次登录，访问所有已暴露服务；移动端友好的登录页 |
| 🏠 **局域网免登录** | 局域网直连不经过网关，家里用完全和以前一样 |
| 🗂️ **路径式路由** | `域名/personal-stock`、`域名/music`……由配置文件驱动 |
| 📄 **黄页** | 访问域名根路径 → 列出所有服务（读配置自动生成） |
| 👥 **用户管理页** | 一个只监听局域网的网页，创建/删除用户、改密码 |
| 💾 **全持久化** | 配置、用户、密钥、会话都在宿主机目录，容器可随时重建 |
| 🔓 **无 2FA** | 用户只要账号密码（管理页用独立密码 + 仅局域网） |

---

## 快速开始

前置条件：

- Docker + Docker Compose v2
- **Tailscale Funnel 已可用**（在 Tailscale 管理后台开启 *HTTPS Certificates* 和 *MagicDNS*）
- 至少一个已在本机端口上跑着的服务（例如家库跑在 `127.0.0.1:3456`）

> 下文出现的 `8080` / `9092` 都是**默认值**，都可以在 `services.yml` 里改
> （见下面「改端口」）。

```bash
# 1. 拉代码（放在任何目录都行，本项目不依赖固定路径）
git clone <你的仓库地址> nas-gateway
cd nas-gateway

# 2. 填域名
cp services.yml.example services.yml
vi services.yml            # 把 domain 改成你的（tailscale status --json | grep -i dnsname）

# 3. 一键部署
./scripts/setup.sh

# 4. 让 Funnel 指向网关
tailscale funnel --bg 8080
#   Tailscale 在容器里的话：
#   docker exec <tailscale容器名> tailscale funnel --bg 8080
```

然后：

1. 浏览器打开 **`http://<NAS的局域网IP>:9092/`** → 设置管理页密码
2. 在管理页里**创建一个用户**（组选 `users`）—— 这就是你登录服务用的账号
3. 打开 **`https://<你的域名>`** → 跳到登录页 → 用刚才的账号登录 → 进入黄页

---

## 目录结构

```
nas-gateway/
├── docker-compose.yml              # 三个容器：caddy / authelia / admin
├── services.yml.example            # ⭐ 主配置模板 → cp 成 services.yml（已 gitignore）
├── .env.example                    # 可选的环境变量覆盖 → cp 成 .env
│
├── caddy/
│   ├── Caddyfile                   # ⛔ 自动生成
│   └── data/ config/               # 运行态（已 gitignore）
│
├── authelia/
│   ├── base.yml                    # ✅ 策略基座（可提交，不含环境信息）
│   ├── configuration.yml           # ⛔ 自动生成
│   ├── users_database.yml.example  # 用户库模板 → cp 成 users_database.yml（已 gitignore）
│   ├── users_database.yml          # 用户库（含密码哈希，已 gitignore）
│   ├── db.sqlite3                  # 会话/状态（已 gitignore）
│   └── secrets/                    # ⛔ 密钥（已 gitignore）
│
├── admin/                          # 用户管理页 + 配置生成器
│   ├── app.py                      # 网页服务（标准库 http.server）
│   ├── cli.py                      # 命令行用户管理
│   ├── generate_config.py          # services.yml → Caddyfile / 配置 / 黄页
│   ├── Dockerfile  requirements.txt
│   └── data/                       # 管理页密码（已 gitignore）
│
├── portal/
│   ├── template.html               # 黄页模板（在 generate_config.py 里）
│   └── index.html                  # ⛔ 自动生成
│
├── scripts/
│   ├── setup.sh                    # 首次部署向导
│   ├── generate-secrets.sh         # 生成密钥（只需跑一次）
│   ├── apply-config.sh             # 改完 services.yml 后应用
│   ├── add-user.sh                 # 命令行加用户（应急）
│   ├── backup.sh                   # 备份配置/用户/密钥
│   └── check.sh                    # 全链路自检
│
└── logs/                           # 日志（已 gitignore）
```

---

## 配置文件 `services.yml`

**唯一需要你编辑的文件。** 改完执行 `./scripts/apply-config.sh`。

```yaml
# 你的公网域名（Tailscale Funnel 提供的）
domain: "fnos-nas-1.warthog-bushi.ts.net"

site:
  title: "我的 NAS"
  subtitle: "私有服务入口"

gateway:
  listen: ":8080"        # Funnel 指向这个端口

admin:
  port: 9092

services:
  - id: personal-stock
    name: "家库"
    desc: "药品与物品库存管理"
    icon: "💊"
    path: /personal-stock          # 访问路径
    upstream: http://127.0.0.1:3456 # 后端（宿主机端口）
    strip_prefix: true             # 转发时剥掉前缀
    auth: required                 # required | bypass
    enabled: true
```

### 加一个新服务

```yaml
  - id: photos
    name: "相册"
    desc: "家庭照片"
    icon: "📷"
    path: /photos
    upstream: http://127.0.0.1:2342
    strip_prefix: true
    auth: required
    enabled: true
```

```bash
./scripts/apply-config.sh
```

> ⚠️ **注意**：走子路径的服务必须自己支持 base path。
> Navidrome / Jellyfin / qBittorrent 等在各自设置里改 Base URL 即可；
> 家库已经支持（会自动读 `X-Forwarded-Prefix`）。

### 改端口

**所有端口都在 `services.yml` 里改**，然后跑 `./scripts/apply-config.sh`：

```yaml
gateway:
  listen: ":18000"       # 网关入口（Funnel 指向这个）

admin:
  port: 18001            # 管理页
  bind: "192.168.1.14"   # 可选：只绑这个地址
```

`apply-config.sh` 会自动：
1. 把 `admin.port` / `admin.bind` **同步进 `.env`**（docker-compose 从这里读）
2. 重载 Caddy（`gateway.listen` 变了会跟着变）
3. 重启 Authelia
4. **端口变了就重建 admin 容器**

> 记得同步改 Funnel：`tailscale funnel --bg 18000`

---

## 用户管理

### 网页（推荐）

```
http://<NAS的局域网IP>:9092/
```

- 首次访问会让你设置**管理页密码**（与用户登录密码无关）
- 可以：创建用户 / 删除用户 / 改密码 / 看操作记录
- **只监听局域网** —— 你家 NAS 没有公网 IP，Funnel 也不暴露这个端口，所以公网访问不到

想更严格，把绑定地址改成 LAN IP：

```bash
# .env
ADMIN_BIND=192.168.1.14
```

### 命令行（应急）

```bash
./scripts/add-user.sh list
./scripts/add-user.sh add 小明 users
./scripts/add-user.sh passwd 小明
./scripts/add-user.sh delete 小明
```

### 组

| 组 | 用途 |
|---|---|
| `users` | 普通用户，用来登录服务（**你和家人都用这个**） |
| `admins` | 管理标记，目前不影响服务内权限 |

> 服务内的细粒度权限（例如"某个用户只能看不许删"）**不由网关决定** ——
> 那需要各个服务自己读 `Remote-User` / `Remote-Groups` 请求头来判断。
> 网关已经把这两个头发给后端了，需要时在对应服务里实现即可。

---

## 局域网免登录是怎么做到的

**不需要任何特殊配置** —— 因为局域网根本不经过网关：

| 访问方式 | 走网关吗 | 要登录吗 |
|---|---|---|
| `http://192.168.1.14:3456`（局域网直连） | ❌ | ❌ |
| `http://100.x.x.x:3456`（Tailscale IP） | ❌ | ❌ |
| `https://<域名>/personal-stock/` | ✅ | ✅ |

也就是说：**只有通过域名的访问才会走认证**。在家用局域网地址，体验和以前一模一样，而且更快（不经代理中转）。

附带好处：Caddy/Authelia 挂了也只影响公网访问，家里照常能用。

---

## Tailscale Funnel

```bash
# 让 Funnel 指向网关
tailscale funnel --bg 8080

# 查看
tailscale funnel status

# 关闭
tailscale funnel reset
```

> Funnel 的公网端口只能是 **443 / 8443 / 10000**，但**本地目标端口任意**（这里是 8080）。
> 所以「`--bg 8080`」是对的：对外是 443，对内转发到 8080。

`funnel status` 应该显示：

```
https://<你的域名> (Funnel on)
|-- / proxy http://127.0.0.1:8080
```

---

## 常用命令

```bash
./scripts/setup.sh              # 首次部署
./scripts/apply-config.sh       # 改完 services.yml 后应用
./scripts/check.sh              # 全链路自检（推荐出问题时先跑这个）
./scripts/backup.sh             # 备份配置/用户/密钥
docker compose ps               # 容器状态
docker compose logs -f caddy    # 日志
docker compose restart caddy    # 重启单个容器
docker compose down             # 停止（数据都在宿主机目录，不会丢）
```

**改了 `docker-compose.yml` 后一定要用 `docker compose up -d`**（`restart` 不会应用配置变更）。

---

## 实现要点（都是实测踩过的坑）

配这个网关时有六个地方特别容易错，这里记下来：

### 1. Authelia 的认证端点路径随版本变

| Authelia 版本 | 端点 |
|---|---|
| **v4.38 及以后** | `/api/authz/forward-auth` ← **当前默认** |
| v4.37 及更早 | `/api/verify` |

写错了会看到 **404**（Authelia 返回 `404 Not Found`，Caddy 原样透传）。
改在 `services.yml` 的 `authelia.auth_endpoint`。

### 2. 必须强制上报 `X-Forwarded-Proto: https`

Tailscale Funnel **终止 TLS**，然后用**明文 HTTP** 转发给 Caddy。Caddy 会如实上报 `http`，
于是 Authelia 报错并返回 **400**：

```
Target URL 'http://...' has an insecure scheme 'http',
only the 'https' and 'wss' schemes are supported
```

所以生成的配置里，`forward_auth` 和 `reverse_proxy` 都带了：

```
header_up X-Forwarded-Proto https
```

**去掉的话公网登录就会 400。**

### 3. 不要在需要认证的路由上写 `request_header -Remote-User`

直觉上应该剥离客户端伪造的内部头，但在 Caddy 里 **`request_header` 晚于 `forward_auth` 执行** ——
它会把刚注入的真实身份又剥掉。

好在 **`copy_headers` 本身就会用 Authelia 返回的值覆盖同名头**（实测：客户端发
`Remote-User: hacker`，后端收到的是 `Remote-User: testuser`）。

所以规则是：

| 路由类型 | 处理 |
|---|---|
| `auth: required` | 靠 `copy_headers` 覆盖，**不要**再加 `request_header -Remote-*` |
| `auth: bypass` | 没有 `forward_auth` 来覆盖，**必须**显式剥离 |

生成器已经按这个规则处理，手改的时候注意。

### 4. 容器要跑成你的 uid（PUID/PGID）

如果容器以 root 运行，`authelia/` 里的文件会变成 `root:root 0600`，
宿主机上的 `./scripts/backup.sh`、编辑器就读不了了。

`docker-compose.yml` 里已经给 `authelia` 和 `admin` 加了：

```yaml
user: "${PUID:-1000}:${PGID:-1000}"
```

`./scripts/setup.sh` 会自动把当前用户的 uid/gid 写进 `.env`。
如果文件属主不对，改 `.env` 里的 `PUID`/`PGID`（用 `id -u` / `id -g` 查），然后
`docker compose up -d`。

### 5. Authelia 挂在子路径 `/authelia/` 下（登录页白屏的元凶）

登录页只显示一行 **"There was an issue retrieving the current user state"**、
浏览器控制台一堆 MIME / 404 报错 —— 基本都是这里没配对。

**原因**：Authelia 的登录页 HTML 里写的是 `<base href="{{ .BaseURL }}" />`，
而 `.BaseURL` 只在 **请求 URI 以配置的路径开头** 时才会被赋值
（`internal/middlewares/strip_path.go`）。配错的话 `<base>` 退化成
`https://<域名>/`，页面里相对路径的 `./static/js/...` 就被解析到
`/static/js/...`（而不是 `/authelia/static/js/...`）→ 资源全挂。

**两处必须同时改**，缺一不可：

| 位置 | 正确写法 | 错误写法 |
|---|---|---|
| `authelia/base.yml` | `address: 'tcp://0.0.0.0:9091/authelia'` | `'tcp://0.0.0.0:9091'`（没有路径） |
| `caddy/Caddyfile` | `handle /authelia/*`（**保留**前缀） | `handle_path /authelia/*`（**会剥掉**前缀） |

另外三条配套的：

- 认证端点按[官方要求](https://www.authelia.com/integration/proxies/introduction/#important-notes)
  走**不带前缀**的 `/api/authz/forward-auth`（Authelia 两个路径都监听，避免一堆坑）。
- `session.cookies[].authelia_url` 末尾**要有斜杠**（`https://<域名>/authelia/`），
  否则重定向地址不带斜杠，浏览器会先落到 `/authelia` 再被 308 跳一次。
- `/authelia`（不带尾斜杠）不匹配 `handle /authelia/*`，会掉进兜底路由被要求登录，
  登录后又跳回 `/authelia` → 死循环。生成器补了一条
  `redir /authelia /authelia/ 308`。

验证方法（应看到带前缀的 base）：

```bash
curl -s https://<你的域名>/authelia/ | grep -o '<base href="[^"]*"'
# 期望：<base href="https://<你的域名>/authelia/" />
```

### 6. 服务块里要用 `route { }` 包住，否则登录后跳错页面

Caddy **不按书写顺序执行指令**，而是按内置指令顺序排序，其中 `uri` 排在
`forward_auth` **前面**。所以这样写：

```
handle /demo* {
    forward_auth 127.0.0.1:9091 { ... }
    uri strip_prefix /demo      # ← 其实先执行了
    reverse_proxy ...
}
```

`uri strip_prefix` 会先跑，`forward_auth` 交给 Authelia 的就是已经剥掉 `/demo` 的路径，
Authelia 据此生成 `?rd=…`。结果是**登录成功后用户被送到站点根目录，而不是他原本
想访问的 `/demo/` 页面**（实测：请求 `/demo/sub/page`，`rd` 变成 `/sub/page`）。

用 `route { }` 包起来即可强制按书写顺序执行：

```
handle /demo* {
    route {
        forward_auth 127.0.0.1:9091 { ... }
        uri strip_prefix /demo
        reverse_proxy ...
    }
}
```

顺带一提：`uri` 后面**不要**再拼 `?rd=<门户地址>`（老文档的写法），
现代 Authelia 会忽略它、自己按 `X-Forwarded-Uri` 算目标地址。

---

## 排错

先跑自检：

```bash
./scripts/check.sh
```

| 现象 | 排查 |
|---|---|
| 域名打不开 | `tailscale funnel status` 是否有输出；Caddy 是否在跑 |
| 一直跳登录页 | 浏览器是否禁用了 Cookie；域名和时间是否都正确 |
| 登录后 403 / 一直循环 | `authelia/configuration.yml` 里 `session.cookies[].domain` 是否等于你的域名 |
| 子路径下页面错乱、资源 404 | 该服务的 base path 没配好（见上面「加一个新服务」的注意） |
| 登录页样式丢失 / 只有 "issue retrieving the current user state" | 见上面「实现要点 5」：`authelia/base.yml` 的 `address` 要带 `/authelia`，Caddy 那边必须是 `handle` 而**不是** `handle_path` |
| 登录成功后跳到首页而不是原本的页面 | 服务块没用 `route { }` 包住 → 见上面「实现要点 6」 |
| 管理页打不开 | `docker compose ps` 看 ng-admin；`curl http://127.0.0.1:9092/healthz` |
| 用户创建了但登录不上 | `authelia/users_database.yml` 是否被写入；Authelia 日志 `docker compose logs authelia` |

看日志：

```bash
docker compose logs --tail=100 caddy
docker compose logs --tail=100 authelia
docker compose logs --tail=100 admin
tail -20 logs/caddy/access.log
```

---

## 安全说明

已做的：

- 🔒 认证只在网关层，后端服务**只监听内网/本机**，公网碰不到
- 🧹 Caddy **剥离**客户端伪造的 `Remote-User` / `Remote-Groups` 等请求头（防身份伪造）
- 🚦 Authelia 登录限流：5 次失败锁 15 分钟
- 🔑 密钥随机生成、权限 600、不入库
- 📝 安全响应头：HSTS / nosniff / X-Frame-Options / Referrer-Policy
- 🚫 默认拒绝：`access_control.default_policy: deny`，只有显式放行的才免登录

需要你知道的：

- **不要暴露管理后台类服务**（Portainer、qBittorrent WebUI、fnOS 管理页、数据库……）
- 网关的 `auth: bypass` 会**完全公开**该路径，慎用
- 备份文件含密钥和密码哈希，**别传到公开的地方**
- 建议定期：`./scripts/backup.sh`、`docker compose pull && docker compose up -d`

---

## 回滚

想临时关掉整个网关（恢复成"只有局域网能用"）：

```bash
tailscale funnel reset          # 公网入口关掉
docker compose down             # 网关容器全部停止
```

局域网访问**从来没受影响**，所以立刻恢复原状。
数据和配置都在宿主机目录里，随时 `docker compose up -d` 回来。

---

## 许可

自用项目，随意取用。
