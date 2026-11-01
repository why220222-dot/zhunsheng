# 部署与运行

## 一、本地运行（现在就能用）

```powershell
cd app
..\启动服务器.bat        # 或 python server.py --port 8899
```

浏览器打开 http://127.0.0.1:8899

- 默认绑定 `0.0.0.0`，同一局域网的其他电脑也能访问
- 端口可用 `--port 9000` 或环境变量 `PORT` 指定

## 二、公网访问

### 方式 A：临时隧道（免费，地址会变）

```powershell
ssh -R 80:127.0.0.1:8899 serveo.net
```

启动后会打印 `https://xxx.serveousercontent.com`。
**必须转发到 `127.0.0.1`**，写 `localhost` 会 502（IPv6 解析问题）。

### 方式 B：部署到 Render（免费，地址固定）

需要：GitHub 账号 + Render 账号（都免费）。

1. 在 GitHub 新建一个空仓库（不要勾选 README）
2. 本机推送代码：

```powershell
cd app
git init
git add .
git commit -m "准绳 首次提交"
git branch -M main
git remote add origin https://github.com/<你的用户名>/zhunsheng.git
git push -u origin main
```

3. 登录 https://render.com → New → Web Service → 选这个仓库
4. Render 会自动读取 `render.yaml`，只需在环境变量里填：
   - `GENESIS_API_KEY`：你的密钥（**不要提交进仓库**）
5. 部署完成后得到 `https://<服务名>.onrender.com`

**免费版注意**：
- 15 分钟无访问会休眠，首次打开需等约 30 秒
- 磁盘是临时的，重启后数据重置（`SEED_DEMO=1` 会自动重新铺演示数据）

## 三、环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `PORT` | 监听端口 | 8899 |
| `HOST` | 绑定地址 | 0.0.0.0 |
| `DEMO_PEOPLE` | 演示人数 | 2 |
| `SEED_DEMO` | 启动时自动载入演示数据（1/0） | 关 |
| `GENESIS_API_KEY` | 模型密钥（必填才走 AI） | 无，走模板兜底 |
| `GENESIS_BASE_URL` | 模型接口 | https://token.moi.matrixorigin.cn/v1 |
| `GENESIS_MODEL` | 主模型 | deepseek-v4.1-flash |

## 四、安全提醒

当前**没有登录鉴权**（用请求头 `X-As` 模拟身份）。放到公网前请至少：

- 不要对外公布地址
- 或者先加一个访问口令（待实现）

数据全在 `app/data/` 下的 JSON 文件里，删除 `state.json` 即等于清空。
