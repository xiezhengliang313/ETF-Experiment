# ETF 策略分析系统

行情同步 → 分红生成 → 回测 → 寻优 → 模拟，一体化研究与实盘跟踪。

- 后端：FastAPI + PostgreSQL（`api_server.py` + `framework/`）
- 前端：React 18 + Ant Design + ECharts + Vite（`frontend/`）
- 页面（侧边栏顺序）：实时行情（ETF 当日快照）、策略总览（策略/账户排行榜）、明细（指标 + 走势 + 持仓 + 交易 + 标的贡献比）、系统设置（数据更新 / 实时行情 / 回测 / 寻优 / 模拟）

## 服务地址（前后端都启动后）

| 服务 | 地址 |
|---|---|
| **前端（主入口）** | **http://localhost:3000** |
| 后端 API | http://127.0.0.1:8000 |
| Swagger 接口文档 | http://127.0.0.1:8000/docs |

---

## 环境要求

| 依赖 | 实测版本 | 说明 |
|---|---|---|
| Python | 3.14.7 | 后端与框架 |
| Node.js | v24.20.0（npm 11.19.0） | 前端 |
| PostgreSQL | 18 | Windows 服务名 `postgresql-x64-18`，端口 5432 |

---

## 首次准备（只需一次）

### 1. 建库

`schema.sql` 是幂等的一键建库脚本（18 张表，可重复执行，不动已有数据）：

```powershell
cd D:\VSCode\ETF-Experiment
psql -U postgres -c "CREATE DATABASE etf"
psql -U postgres -d etf -f schema.sql
```

### 2. 配置 `.env`

放在**项目根目录**（与 `api_server.py` 同级，代码用 `Path(__file__).with_name(".env")` 定位）：

```
PG_HOST=localhost
PG_PORT=5432
PG_DB=etf
PG_USER=postgres
PG_PASSWORD=你的密码
```

### 3. Python 依赖

项目暂无 `requirements.txt`，按下列包安装：

```powershell
pip install "psycopg[binary]" pandas numpy scipy fastapi uvicorn pydantic
```

### 4. 前端依赖

```powershell
cd D:\VSCode\ETF-Experiment\frontend
npm install
```

---

## 启动

需要**两个终端**，前后端都要跑。

### 终端 1 —— 后端（必须在项目根目录，因为要用 framework 包）

```powershell
cd D:\VSCode\ETF-Experiment
python -m uvicorn api_server:app --host 127.0.0.1 --port 8000
# 开发时想改代码自动重载，加 --reload
```

### 终端 2 —— 前端

```powershell
cd D:\VSCode\ETF-Experiment\frontend
npm run dev
```

浏览器打开 **http://localhost:3000**。

---

## 验证

```powershell
# 后端：应返回 status / db / latest_trade_date / tables
Invoke-RestMethod http://127.0.0.1:8000/api/health

# 前端：应返回 200
Invoke-WebRequest http://localhost:3000
```

接口文档（Swagger）：http://127.0.0.1:8000/docs

---

## 注意事项

1. **两个服务都要跑**。后端只提供 `/api` 接口，**不托管前端静态文件**（`api_server.py` 里没有 `StaticFiles`/`mount`）；前端 Vite 把 `/api` 代理到 `127.0.0.1:8000`。只开前端会报 proxy `ECONNREFUSED`。
2. **`.env` 必须在项目根目录**。和你在哪个目录敲命令无关，但文件位置不能挪。
3. **端口**：后端 8000、前端 3000、PostgreSQL 5432。端口被占用时先查 `Get-NetTCPConnection -LocalPort 8000 -State Listen`。

---

## 目录结构

```
ETF-Experiment/
├─ .env                      # 数据库连接配置（不要提交）
├─ api_server.py             # FastAPI 后端，/api/* 接口 + 任务调度
├─ schema.sql                # 一键建库脚本（幂等，22 张表）
├─ docs/                     # 系统设计方案 / FastAPI方案 / React方案
├─ framework/                # 业务框架（python -m framework.xxx 运行）
│  ├─ etf_sync.py            #   行情同步
│  ├─ etf_quote.py           #   实时行情抓取（腾讯 qt.gtimg.cn → etf_quote）
│  ├─ gen_corp_actions.py    #   公司行为生成（分红+折算）
│  ├─ backtest_framework.py  #   回测
│  ├─ optimize_framework.py  #   参数寻优
│  └─ simulate_framework.py  #   模拟账户
└─ frontend/                 # React + Vite 前端
   ├─ vite.config.ts         #   端口 3000，/api 代理到 8000
   └─ src/
      ├─ pages/              #   Dashboard / Detail / Settings
      ├─ components/         #   EChart / MetricsCard / NavChart / HoldingStack ...
      └─ api/                #   axios 封装，与后端接口一一对应
```

---

## 常用操作

```powershell
# 前端生产构建（产物在 frontend/dist）
cd frontend
npm run build

# 前端代码检查
npm run lint
```

日常数据流程：**系统设置 → 数据更新（同步行情 → 生成分红/折算）→ 回测策略（跑回测）→ 寻优 → 模拟**。

策略的 `end_date` **留空 = 跟行情最新交易日**（跟模拟账户同一套写法）：同步行情后跑一次回测，就会把净值、持仓与指标**全量重算**到最新交易日，不需要手工延期。

---

## 策略一览（数据快照，生成于 2026-10-10 21:55:47）

> 与前端「策略总览 → 策略一览」表同口径：按**年化收益**降序；数值随「回测刷新」变化，需手工更新。

| 名称 | 当日 | 近一周 | 近一月 | 近三月 | 近一年 | 近两年 | 近三年 | 全期 | 年化 | 回撤 | 夏普 | 波动率 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 趋势轮动 | +1.01% | -5.12% | -5.98% | -8.73% | +21.68% | +113.75% | +134.56% | +627.90% | +30.45% | -25.35% | 1.08 | +28.21% |
| 全天候-最大夏普 | +0.49% | +2.61% | +3.71% | -2.12% | +24.87% | +67.40% | +100.38% | +411.86% | +24.44% | -16.71% | 1.40 | +16.71% |
| 全天候-最小方差 | +0.10% | +0.29% | -0.36% | +7.42% | +9.92% | +51.16% | +69.61% | +189.73% | +15.31% | -16.56% | 1.33 | +11.25% |
| 风格轮动-最小方差 | -0.13% | -0.49% | -1.48% | +0.00% | +4.40% | +41.47% | +34.16% | +130.42% | +11.83% | -24.15% | 0.80 | +15.67% |
| 行业轮动-最大夏普 | -0.70% | +0.09% | -0.17% | -10.96% | -10.09% | +27.82% | +14.96% | +109.64% | +10.42% | -35.01% | 0.57 | +21.80% |
| 风格轮动-最大夏普 | +0.53% | -1.22% | -3.06% | -11.28% | -10.05% | +10.59% | +13.97% | +109.32% | +10.40% | -22.47% | 0.62 | +19.42% |
| 行业轮动-最小方差 | +0.11% | +0.16% | -1.18% | +2.35% | +3.19% | +36.81% | +28.61% | +93.42% | +9.24% | -24.30% | 0.63 | +16.59% |
