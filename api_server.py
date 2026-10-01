# -*- coding: utf-8 -*-
"""FastAPI 接口层：把 5 个框架包成 REST + JSON 给 React 前端用。

只做两件事：
  1) 读接口：直接查 PostgreSQL 返回 JSON（同步，快）；
  2) 触发接口：把「跑脚本」交给后台任务，返回 task_id（异步，前端轮询）。

不改动任何现有框架逻辑；框架本身直接 import 复用。

运行：
    pip install fastapi uvicorn
    uvicorn api_server:app --port 8000
    （或 python api_server.py）

注意：寻优/回测/模拟是 CPU 密集 + 会写同一批表，后台任务采用全局单任务锁
（同一时刻只允许一个任务在跑），新任务到来直接标记 error。
"""

import asyncio
import datetime as dt
import decimal
import os
import traceback
import uuid
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path

import psycopg
import psycopg.errors
from psycopg.rows import dict_row
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from framework.etf_sync import EtfSync
from framework.gen_dividends import DividendBuilder
from framework.backtest_framework import BacktestFramework, sync_strategy_name
from framework.optimize_framework import OptimizeFramework
from framework.simulate_framework import SimulateFramework, sync_segment_name

app = FastAPI(title="ETF 分析系统 API")

# 本地开发：允许 Vite(3000) 直连（推荐用 Vite 代理，则无需 CORS）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

METHODS = ("max_sharpe", "min_variance", "trend")
FAR_FUTURE = dt.date(9999, 12, 31)  # 分段 end_date = 跑到账户结束


# ================================================================ 数据库连接
def _load_env():
    p = Path(__file__).with_name(".env")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


@contextmanager
def db():
    """每个请求开一个连接，用完即关（本地单用户足够）。"""
    _load_env()
    conn = psycopg.connect(
        host=os.environ.get("PG_HOST", "localhost"),
        port=os.environ.get("PG_PORT", "5432"),
        dbname=os.environ.get("PG_DB", "etf"),
        user=os.environ.get("PG_USER", "postgres"),
        password=os.environ.get("PG_PASSWORD", ""),
        row_factory=dict_row,
    )
    try:
        yield conn
    finally:
        conn.close()


# ================================================================ 序列化
def _dump(obj):
    """把 psycopg 返回的 Decimal/date/datetime/jsonb 转成 JSON 可序列化类型。"""
    if isinstance(obj, dict):
        return {k: _dump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dump(x) for x in obj]
    if isinstance(obj, dt.datetime):
        return obj.isoformat()
    if isinstance(obj, dt.date):
        return obj.isoformat()
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    return obj


def _query(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [_dump(r) for r in cur.fetchall()]


def _one(conn, sql, params=()):
    rows = _query(conn, sql, params)
    return rows[0] if rows else None


def _as_date(v):
    """_dump 会把 date 转成 'YYYY-MM-DD' 字符串，所以比较/加减前先还原成 date。"""
    if v is None or isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def _drawdown(vals):
    """从净值序列算逐日回撤（负数，0=创新高，-0.2687=回撤26.87%），与框架 max_drawdown 口径一致。"""
    peak, out = None, []
    for v in vals:
        if v is None:
            out.append(None)
            continue
        peak = v if (peak is None or v > peak) else peak
        out.append(0.0 if peak == 0 else (v - peak) / peak)
    return out


# ================================================================ 后台任务管理器
TASKS = {}
_RUN_LOCK = asyncio.Lock()


async def submit(name, fn):
    tid = uuid.uuid4().hex[:8]
    if _RUN_LOCK.locked():
        TASKS[tid] = {"task_id": tid, "name": name, "status": "error",
                      "log": "已有任务在运行，请等它结束",
                      "started_at": None, "finished_at": None}
        return tid
    TASKS[tid] = {"task_id": tid, "name": name, "status": "queued",
                  "log": "", "started_at": None, "finished_at": None}
    asyncio.create_task(_run(tid, fn))
    return tid


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


async def _run(tid, fn):
    async with _RUN_LOCK:
        TASKS[tid]["status"] = "running"
        TASKS[tid]["started_at"] = _now()
        buf = StringIO()
        try:
            with redirect_stdout(buf):          # 抓框架 print 当运行日志
                await asyncio.to_thread(fn)      # CPU 密集代码丢线程池，不卡事件循环
            TASKS[tid]["status"] = "done"
            TASKS[tid]["log"] = buf.getvalue()
        except Exception:
            TASKS[tid]["status"] = "error"
            TASKS[tid]["log"] = traceback.format_exc()
        finally:
            TASKS[tid]["finished_at"] = _now()


# ================================================================ 校验
def _validate_strategy_fields(method, period=None, lookback=None, min_w=None,
                              max_w=None, long_window=None, short_window=None):
    if method not in METHODS:
        raise HTTPException(400, f"strategy_method 必须是 {METHODS}")
    if period is None or period <= 0:
        raise HTTPException(400, "period 必须 > 0")
    if method in ("max_sharpe", "min_variance"):
        if lookback is None or min_w is None or max_w is None:
            raise HTTPException(400, f"{method} 需要 lookback/min_w/max_w")
    else:
        if long_window is None or short_window is None:
            raise HTTPException(400, "trend 需要 long_window/short_window")
        if not short_window < long_window:
            raise HTTPException(400, "short_window 必须 < long_window")
    if min_w is not None and min_w < 0:
        raise HTTPException(400, "min_w 不能 < 0")
    if max_w is not None and max_w > 1:
        raise HTTPException(400, "max_w 不能 > 1")
    if min_w is not None and max_w is not None and min_w > max_w:
        raise HTTPException(400, "min_w 不能 > max_w")


# ================================================================ 请求体模型
class StrategyIn(BaseModel):
    strategy_name: str | None = None
    strategy_method: str
    period: int | None = None
    lookback: int | None = None
    min_w: float | None = None
    max_w: float | None = None
    long_window: int | None = None
    short_window: int | None = None
    codes: list[str]
    start_date: dt.date
    end_date: dt.date | None = None      # 留空 = 跟行情最新交易日
    benchmark: str = "sh510300"
    is_active: bool = True
    note: str | None = None


class StrategyPatch(BaseModel):
    """只允许改非结果字段（名称/备注/有效性）。就地改引擎参数会污染结果表。"""
    strategy_name: str | None = None
    note: str | None = None
    is_active: bool | None = None


class OptimizeIn(BaseModel):
    workers: int = 0


class AccountIn(BaseModel):
    account_name: str | None = None
    init_cash: float = 1_000_000.0
    start_date: dt.date
    end_date: dt.date | None = None      # NULL = 跟最新交易日
    benchmark: str = "sh510300"
    is_active: bool = True
    note: str | None = None


class AccountPatch(BaseModel):
    account_name: str | None = None
    note: str | None = None
    is_active: bool | None = None


class SegmentIn(BaseModel):
    strategy_name: str | None = None
    strategy_method: str | None = None   # 显式填；copy_from 时忽略
    period: int | None = None
    lookback: int | None = None
    min_w: float | None = None
    max_w: float | None = None
    long_window: int | None = None
    short_window: int | None = None
    codes: list[str] | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    copy_from_strategy_id: int | None = None  # 从 bt_params 复制方法/参数/成分


class SegmentPatch(BaseModel):
    strategy_name: str | None = None
    end_date: dt.date | None = None       # 提前结束某段；sim_params.end_date NOT NULL，所以别传 null
    note: str | None = None


# ================================================================ 5.1 系统
@app.get("/api/health")
def health():
    try:
        with db() as conn:
            latest = _one(conn, "SELECT max(trade_date) AS d FROM etf_bars_raw")["d"]
            tables = {}
            for t in ("bt_params", "sim_account", "sim_params", "opt_results", "opt_walk", "opt_folds",
                      "opt_oos_nav", "bt_nav", "sim_nav", "etf_bars_raw", "etf_dividends",
                      "trade_calendar"):
                tables[t] = _one(conn, f"SELECT count(*) AS c FROM {t}")["c"]
        return {"status": "ok", "db": "connected",
                "latest_trade_date": latest, "tables": tables,
                "server_time": _now()}
    except Exception as e:
        raise HTTPException(503, f"数据库连接失败：{e}")


# ================================================================ 5.2 行情 & 分红
@app.get("/api/market/codes")
def market_codes():
    with db() as conn:
        return _query(conn,
                      "SELECT DISTINCT code, name FROM etf_bars_raw ORDER BY code")


@app.post("/api/market/sync", status_code=202)
async def market_sync():
    tid = await submit("行情同步", lambda: EtfSync().run())
    return {"task_id": tid}


@app.get("/api/dividends")
def dividends(code: str | None = None):
    with db() as conn:
        if code:
            return _query(conn,
                          "SELECT code, ex_date, div_per_share, source "
                          "FROM etf_dividends WHERE code = %s ORDER BY ex_date", (code,))
        return _query(conn,
                      "SELECT code, ex_date, div_per_share, source "
                      "FROM etf_dividends ORDER BY code, ex_date")


@app.post("/api/dividends/generate", status_code=202)
async def dividends_generate():
    tid = await submit("分红生成", lambda: DividendBuilder().run())
    return {"task_id": tid}


# ================================================================ 5.3 回测
# 指标列直接复用回测框架的 METRIC_COLS（唯一真源），避免「框架加了列、接口没跟」这类漂移
_METRIC_SQL = ", ".join(BacktestFramework.METRIC_COLS)
_STRATEGY_COLS = (
    "strategy_id, strategy_name, strategy_method, period, lookback, min_w, max_w, "
    "long_window, short_window, codes, start_date, end_date, benchmark, source, "
    "opt_id, is_active, note, metrics_at, " + _METRIC_SQL
)


@app.get("/api/backtest/strategies")
def bt_strategies(is_active: bool | None = None):
    with db() as conn:
        if is_active is None:
            return _query(conn, f"SELECT {_STRATEGY_COLS} FROM bt_params ORDER BY strategy_id")
        return _query(conn, f"SELECT {_STRATEGY_COLS} FROM bt_params "
                            "WHERE is_active = %s ORDER BY strategy_id", (is_active,))


@app.post("/api/backtest/strategies")
def bt_strategy_post(body: StrategyIn):
    _validate_strategy_fields(body.strategy_method, body.period, body.lookback,
                              body.min_w, body.max_w, body.long_window, body.short_window)
    codes = sorted(set(body.codes))
    with db() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO bt_params (strategy_name, strategy_method, period, lookback, "
                    "min_w, max_w, long_window, short_window, codes, start_date, end_date, "
                    "benchmark, is_active, note, source) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'manual') "
                    "RETURNING strategy_id",
                    (body.strategy_name, body.strategy_method, body.period, body.lookback,
                     body.min_w, body.max_w, body.long_window, body.short_window,
                     codes, body.start_date, body.end_date, body.benchmark,
                     body.is_active, body.note))
                sid = cur.fetchone()["strategy_id"]
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                raise HTTPException(400, "策略重复：相同方法+参数+成分+起止的组合已存在")
        conn.commit()
        return {"strategy_id": sid}


def _patch(conn, table, pk, pkid, fields):
    if not fields:
        return _one(conn, f"SELECT * FROM {table} WHERE {pk} = %s", (pkid,))
    sets = ", ".join(f"{k} = %s" for k in fields)
    with conn.cursor() as cur:
        cur.execute(f"UPDATE {table} SET {sets} WHERE {pk} = %s RETURNING *",
                    list(fields.values()) + [pkid])
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, f"{table} 不存在 {pkid}")
    conn.commit()
    return _dump(dict(row))


@app.put("/api/backtest/strategies/{sid}")
def bt_strategy_patch(sid: int, body: StrategyPatch):
    fields = body.model_dump(exclude_unset=True)
    with db() as conn:
        row = _patch(conn, "bt_params", "strategy_id", sid, fields)
        if "strategy_name" in fields:
            # 结果表里的 strategy_name 只是副本，改名后必须同步，否则历史行留旧名
            sync_strategy_name(conn, sid, row["strategy_name"])
        return row


@app.delete("/api/backtest/strategies/{sid}")
def bt_strategy_delete(sid: int):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bt_params WHERE strategy_id = %s", (sid,))
            if cur.rowcount == 0:
                raise HTTPException(404, "策略不存在")
        conn.commit()
        return {"deleted": sid}


@app.post("/api/backtest/run", status_code=202)
async def backtest_run():
    tid = await submit("回测", lambda: BacktestFramework().run())
    return {"task_id": tid}


def _curve_points(strategy_nav, benchmark_nav):
    bm = {r["trade_date"]: r["nav"] for r in benchmark_nav}
    dates = [r["trade_date"] for r in strategy_nav]
    navs = [r["nav"] for r in strategy_nav]
    bmnavs = [bm.get(d) for d in dates]
    dd, bd = _drawdown(navs), _drawdown(bmnavs)
    curve = [{"trade_date": d, "nav": n, "benchmark_nav": b}
             for d, n, b in zip(dates, navs, bmnavs)]
    drawdown = [{"trade_date": d, "strategy_dd": a, "benchmark_dd": b}
                for d, a, b in zip(dates, dd, bd)]
    return curve, drawdown


@app.get("/api/backtest/strategies/{sid}/curve")
def bt_curve(sid: int):
    with db() as conn:
        nav = _query(conn, "SELECT trade_date, nav FROM bt_nav WHERE strategy_id = %s ORDER BY trade_date", (sid,))
        bm = _query(conn, "SELECT trade_date, nav FROM bt_benchmark_nav WHERE strategy_id = %s ORDER BY trade_date", (sid,))
    if not nav:
        raise HTTPException(404, "该策略尚无回测结果")
    return _curve_points(nav, bm)[0]


@app.get("/api/backtest/strategies/{sid}/drawdown")
def bt_drawdown(sid: int):
    with db() as conn:
        nav = _query(conn, "SELECT trade_date, nav FROM bt_nav WHERE strategy_id = %s ORDER BY trade_date", (sid,))
        bm = _query(conn, "SELECT trade_date, nav FROM bt_benchmark_nav WHERE strategy_id = %s ORDER BY trade_date", (sid,))
    if not nav:
        raise HTTPException(404, "该策略尚无回测结果")
    return _curve_points(nav, bm)[1]


def _return_points(strategy_nav, benchmark_nav):
    """逐日涨跌幅（今日净值 / 上一交易日净值 - 1），策略与基准按同一批交易日对齐。
    第一天没有上一日 → 不产出（避免一条假的 0 收益混进分布图）。"""
    bm = {r["trade_date"]: r["nav"] for r in benchmark_nav}
    rows = [(r["trade_date"], r["nav"], bm.get(r["trade_date"])) for r in strategy_nav]
    out = []
    for (_, n0, b0), (d, n, b) in zip(rows, rows[1:]):
        out.append({
            "trade_date": d,
            "ret": None if not n0 else n / n0 - 1.0,
            "benchmark_ret": None if not (b0 and b) else b / b0 - 1.0,
        })
    return out


@app.get("/api/backtest/strategies/{sid}/returns")
def bt_returns(sid: int):
    with db() as conn:
        nav = _query(conn, "SELECT trade_date, nav FROM bt_nav WHERE strategy_id = %s ORDER BY trade_date", (sid,))
        bm = _query(conn, "SELECT trade_date, nav FROM bt_benchmark_nav WHERE strategy_id = %s ORDER BY trade_date", (sid,))
    if not nav:
        raise HTTPException(404, "该策略尚无回测结果")
    return _return_points(nav, bm)


@app.get("/api/backtest/strategies/{sid}/nav")
def bt_nav(sid: int):
    with db() as conn:
        return _query(conn,
                      "SELECT trade_date, nav, cash, market_value, total "
                      "FROM bt_nav WHERE strategy_id = %s ORDER BY trade_date", (sid,))


@app.get("/api/backtest/strategies/{sid}/benchmark")
def bt_benchmark(sid: int):
    with db() as conn:
        return _query(conn,
                      "SELECT trade_date, nav FROM bt_benchmark_nav "
                      "WHERE strategy_id = %s ORDER BY trade_date", (sid,))


@app.get("/api/backtest/strategies/{sid}/trades")
def bt_trades(sid: int):
    with db() as conn:
        return _query(conn,
                      "SELECT trade_date, code, name, side, qty, price, amount, fee "
                      "FROM bt_trades WHERE strategy_id = %s ORDER BY trade_date, code", (sid,))


@app.get("/api/backtest/strategies/{sid}/holdings")
def bt_holdings(sid: int):
    with db() as conn:
        return _query(conn,
                      "SELECT rebal_date, code, name, weight, signal_close, signal_equity, target_qty "
                      "FROM bt_holdings WHERE strategy_id = %s ORDER BY rebal_date, code", (sid,))


@app.get("/api/backtest/strategies/{sid}/metrics")
def bt_metrics(sid: int):
    with db() as conn:
        row = _one(conn,
                   f"SELECT strategy_id, strategy_name, {_METRIC_SQL} "
                   "FROM bt_params WHERE strategy_id = %s", (sid,))
    if row is None:
        raise HTTPException(404, "策略不存在")
    return row


@app.get("/api/backtest/strategies/{sid}/calendar")
def bt_calendar(sid: int):
    with db() as conn:
        latest = _one(conn, "SELECT max(trade_date) AS d FROM etf_bars_raw")["d"]
        rows = _query(conn,
                      "SELECT rebal_date, signal_date FROM bt_calendar "
                      "WHERE strategy_id = %s ORDER BY rebal_date", (sid,))
    for r in rows:
        r["is_future"] = latest is not None and r["rebal_date"] > latest
    return rows


# ================================================================ 5.4 寻优
@app.get("/api/optimize/results")
def opt_results(opt_id: int | None = None, selected_only: bool = True,
                limit: int = 200, offset: int = 0):
    where, params = ["1=1"], []
    if opt_id is not None:
        where.append("opt_id = %s"); params.append(opt_id)
    if selected_only:
        where.append("selected")
    sql = (f"SELECT * FROM opt_results WHERE {' AND '.join(where)} "
           "ORDER BY opt_id DESC, candidate LIMIT %s OFFSET %s")
    with db() as conn:
        return _query(conn, sql, params + [limit, offset])


@app.get("/api/optimize/winners")
def opt_winners():
    """寻优胜出参数，一次寻优一行（新的在前）。

    两个来源必须拼成同一份列集，所以用 UNION ALL：
    - 滚动寻优批次（opt_walk）：basis='oos'，指标是**样本外拼接**口径，folds/winner_folds 有值
      （winner_folds = 晋级参数里最弱参数进过前 10% 候选的折数，见 OptimizeFramework.stability_vote）；
    - 旧方法批次或样本太短退回的单段寻优：basis='full'，指标是**全样本**口径，折数为 NULL。
    每折训练窗的胜出参数也在 opt_results 里标了 selected，但那不是晋级参数，
    这里用 NOT EXISTS(opt_folds.is_opt_id) 把它们排除掉。
    """
    m = OptimizeFramework.OPT_METRIC_COLS
    walk_cols = ", ".join(f"w.oos_{c} AS {c}" for c in m)
    full_cols = ", ".join(f"o.{c}" for c in m)
    sql = (
        "SELECT w.opt_id, w.strategy_method, w.codes, w.start_date, w.end_date, "
        "w.winner_params AS params, w.folds, w.winner_folds, w.train_days, w.test_days, w.step_days, "
        "'oos' AS basis, "
        # 综合分 = 晋级参数在各折训练窗的综合分均值。不能只取 selected 那几折的平均：
        # 晋级参数是投影出来的，常常不是任何一折的胜者，那样会算成 NULL
        "(SELECT avg(r.score) FROM opt_results r JOIN opt_folds f ON f.is_opt_id = r.opt_id "
        "WHERE f.opt_id = w.opt_id AND r.params = w.winner_params) AS score, "
        + walk_cols + ", "
        "(SELECT b.strategy_name FROM bt_params b WHERE b.opt_id = w.opt_id LIMIT 1) AS strategy_name "
        "FROM opt_walk w "
        "UNION ALL "
        "SELECT o.opt_id, o.strategy_method, o.codes, o.start_date, o.end_date, o.params, "
        "NULL::int, NULL::int, NULL::int, NULL::int, NULL::int, 'full' AS basis, o.score, "
        + full_cols + ", "
        "(SELECT b.strategy_name FROM bt_params b WHERE b.opt_id = o.opt_id LIMIT 1) AS strategy_name "
        "FROM opt_results o WHERE o.selected "
        "AND NOT EXISTS (SELECT 1 FROM opt_folds f WHERE f.is_opt_id = o.opt_id) "
        "ORDER BY opt_id DESC")
    with db() as conn:
        return _query(conn, sql)


@app.get("/api/optimize/walk/{opt_id}/folds")
def opt_walk_folds(opt_id: int):
    """滚动寻优的每折明细：训练窗选出的参数 + 训练窗/样本外窗两套指标，按折号升序。"""
    with db() as conn:
        return _query(conn, "SELECT * FROM opt_folds WHERE opt_id = %s ORDER BY fold", (opt_id,))


@app.get("/api/optimize/walk/{opt_id}/oos")
def opt_walk_oos(opt_id: int):
    """样本外拼接净值曲线：一行一个交易日（trade_date / nav / fold），按交易日升序。

    fold = 该日来自第几折，前端据此画折分界竖线、悬停显示该折参数。
    曲线起点恒为 1.0，与 opt_walk.oos_* 指标同源（同一串净值算出来的）。
    """
    with db() as conn:
        return _query(conn, "SELECT trade_date, nav, fold FROM opt_oos_nav "
                            "WHERE opt_id = %s ORDER BY trade_date", (opt_id,))


@app.post("/api/optimize/run", status_code=202)
async def optimize_run(body: OptimizeIn | None = None):
    workers = body.workers if body else 0
    tid = await submit("寻优",
                       lambda: OptimizeFramework(workers=workers if workers > 0 else None).run())
    return {"task_id": tid}


# ================================================================ 5.5 模拟（账户化）
@app.get("/api/sim/accounts")
def sim_accounts():
    with db() as conn:
        return _query(conn, "SELECT * FROM sim_account ORDER BY account_id")


@app.post("/api/sim/accounts")
def sim_account_post(body: AccountIn):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sim_account (account_name, init_cash, start_date, end_date, "
                "benchmark, is_active, note) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING account_id",
                (body.account_name, body.init_cash, body.start_date, body.end_date,
                 body.benchmark, body.is_active, body.note))
            aid = cur.fetchone()["account_id"]
        conn.commit()
        return {"account_id": aid}


@app.put("/api/sim/accounts/{aid}")
def sim_account_patch(aid: int, body: AccountPatch):
    fields = body.model_dump(exclude_unset=True)
    with db() as conn:
        return _patch(conn, "sim_account", "account_id", aid, fields)


@app.delete("/api/sim/accounts/{aid}")
def sim_account_delete(aid: int):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sim_account WHERE account_id = %s", (aid,))
            if cur.rowcount == 0:
                raise HTTPException(404, "账户不存在")
        conn.commit()
        return {"deleted": aid}


@app.get("/api/sim/accounts/{aid}/segments")
def sim_segments(aid: int):
    with db() as conn:
        return _query(conn, "SELECT * FROM sim_params WHERE account_id = %s "
                            "ORDER BY start_date, strategy_id", (aid,))


# 各模拟结果表里的「日期」列名不一样
_SIM_DATE_COL = {"sim_nav": "trade_date", "sim_trades": "trade_date",
                 "sim_benchmark_nav": "trade_date",
                 "sim_holdings": "rebal_date", "sim_calendar": "rebal_date"}


def _invalidate_sim_results(conn, aid: int, cut: dt.date) -> int:
    """把该账户从 cut 天起的模拟结果删掉（历史保留）。
    分段一改，只有 cut 之后的结果可能不一样；删掉后「模拟刷新」走增量路径自动补算。"""
    n = 0
    with conn.cursor() as cur:
        for table, col in _SIM_DATE_COL.items():
            cur.execute(f"DELETE FROM {table} WHERE account_id = %s AND {col} >= %s", (aid, cut))
            n += cur.rowcount
        if n:
            # 账户指标是按「全量净值序列」算的 → 结果被砍掉就必须重算，先清空（前端显示 —）
            sets = ", ".join(f"{c} = NULL" for c in BacktestFramework.METRIC_COLS)
            cur.execute(f"UPDATE sim_account SET {sets}, metrics_at = NULL WHERE account_id = %s", (aid,))
    conn.commit()
    return n


@app.post("/api/sim/accounts/{aid}/segments")
def sim_segment_post(aid: int, body: SegmentIn):
    with db() as conn:
        acct = _one(conn, "SELECT * FROM sim_account WHERE account_id = %s", (aid,))
        if acct is None:
            raise HTTPException(404, "账户不存在")
        if body.copy_from_strategy_id is not None:
            src = _one(conn, "SELECT * FROM bt_params WHERE strategy_id = %s",
                       (body.copy_from_strategy_id,))
            if src is None:
                raise HTTPException(404, "被复制的回测策略不存在")
            method, codes = src["strategy_method"], src["codes"]
            period, lookback = src["period"], src["lookback"]
            min_w, max_w = src["min_w"], src["max_w"]
            long_window, short_window = src["long_window"], src["short_window"]
            start = body.start_date or acct["start_date"]
            end = body.end_date or FAR_FUTURE
            name = body.strategy_name or src["strategy_name"]
        else:
            if not body.strategy_method or not body.codes:
                raise HTTPException(400, "需 strategy_method 或 copy_from_strategy_id")
            method, codes = body.strategy_method, sorted(set(body.codes))
            period, lookback = body.period, body.lookback
            min_w, max_w = body.min_w, body.max_w
            long_window, short_window = body.long_window, body.short_window
            if body.start_date is None or body.end_date is None:
                raise HTTPException(400, "显式新建分段需 start_date + end_date")
            start, end, name = body.start_date, body.end_date, body.strategy_name
        _validate_strategy_fields(method, period, lookback, min_w, max_w,
                                  long_window, short_window)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sim_params (account_id, account_name, strategy_name, strategy_method, "
                "period, lookback, min_w, max_w, long_window, short_window, codes, start_date, end_date) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING strategy_id",
                (aid, acct["account_name"], name, method, period, lookback,
                 min_w, max_w, long_window, short_window, codes, start, end))
            sid = cur.fetchone()["strategy_id"]
        conn.commit()
        return {"strategy_id": sid}


@app.put("/api/sim/accounts/{aid}/segments/{sid}")
def sim_segment_patch(aid: int, sid: int, body: SegmentPatch):
    fields = body.model_dump(exclude_unset=True)
    with db() as conn:
        old = _one(conn, "SELECT * FROM sim_params WHERE strategy_id = %s AND account_id = %s", (sid, aid))
        if old is None:
            raise HTTPException(404, "分段不存在")
        row = _patch(conn, "sim_params", "strategy_id", sid, fields)
        if row is None or row.get("account_id") != aid:
            raise HTTPException(404, "分段不存在")
        if "strategy_name" in fields:
            # 同上：sim_* 结果表里的分段名副本要同步
            sync_segment_name(conn, sid, row["strategy_name"])
        # 结束日变了 → 该账户从「新旧两个结束日里较早那天的次日」起的结果作废：
        # 那天之前的分段构成完全没变，历史行必然一样，保留；之后交给「模拟刷新」按增量补算。
        if "end_date" in fields:
            old_end, new_end = _as_date(old["end_date"]), _as_date(fields["end_date"])
            if old_end != new_end:
                cut = min(old_end, new_end) + dt.timedelta(days=1)
                row["invalidated_rows"] = _invalidate_sim_results(conn, aid, cut)
                row["recompute_from"] = cut
        return row


@app.delete("/api/sim/accounts/{aid}/segments/{sid}")
def sim_segment_delete(aid: int, sid: int):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sim_params WHERE account_id = %s AND strategy_id = %s", (aid, sid))
            if cur.rowcount == 0:
                raise HTTPException(404, "分段不存在")
        conn.commit()
        return {"deleted": sid}


@app.post("/api/sim/run", status_code=202)
async def sim_run():
    tid = await submit("模拟", lambda: SimulateFramework().run())
    return {"task_id": tid}


@app.get("/api/sim/accounts/{aid}/curve")
def sim_curve(aid: int):
    with db() as conn:
        nav = _query(conn, "SELECT trade_date, nav FROM sim_nav WHERE account_id = %s ORDER BY trade_date", (aid,))
        bm = _query(conn, "SELECT trade_date, nav FROM sim_benchmark_nav WHERE account_id = %s ORDER BY trade_date", (aid,))
    if not nav:
        raise HTTPException(404, "该账户尚无模拟结果")
    return _curve_points(nav, bm)[0]


@app.get("/api/sim/accounts/{aid}/drawdown")
def sim_drawdown(aid: int):
    with db() as conn:
        nav = _query(conn, "SELECT trade_date, nav FROM sim_nav WHERE account_id = %s ORDER BY trade_date", (aid,))
        bm = _query(conn, "SELECT trade_date, nav FROM sim_benchmark_nav WHERE account_id = %s ORDER BY trade_date", (aid,))
    if not nav:
        raise HTTPException(404, "该账户尚无模拟结果")
    return _curve_points(nav, bm)[1]


@app.get("/api/sim/accounts/{aid}/returns")
def sim_returns(aid: int):
    with db() as conn:
        nav = _query(conn, "SELECT trade_date, nav FROM sim_nav WHERE account_id = %s ORDER BY trade_date", (aid,))
        bm = _query(conn, "SELECT trade_date, nav FROM sim_benchmark_nav WHERE account_id = %s ORDER BY trade_date", (aid,))
    if not nav:
        raise HTTPException(404, "该账户尚无模拟结果")
    return _return_points(nav, bm)


@app.get("/api/sim/accounts/{aid}/nav")
def sim_nav(aid: int):
    with db() as conn:
        return _query(conn,
                      "SELECT trade_date, nav, cash, market_value, dividend, total "
                      "FROM sim_nav WHERE account_id = %s ORDER BY trade_date", (aid,))


@app.get("/api/sim/accounts/{aid}/benchmark")
def sim_benchmark(aid: int):
    with db() as conn:
        return _query(conn,
                      "SELECT trade_date, nav FROM sim_benchmark_nav "
                      "WHERE account_id = %s ORDER BY trade_date", (aid,))


@app.get("/api/sim/accounts/{aid}/trades")
def sim_trades(aid: int, strategy_id: int | None = None):
    with db() as conn:
        if strategy_id is not None:
            return _query(conn,
                          "SELECT strategy_id, strategy_name, trade_date, code, name, side, "
                          "qty, price, amount, fee FROM sim_trades "
                          "WHERE account_id = %s AND strategy_id = %s ORDER BY trade_date, code",
                          (aid, strategy_id))
        return _query(conn,
                      "SELECT strategy_id, strategy_name, trade_date, code, name, side, "
                      "qty, price, amount, fee FROM sim_trades "
                      "WHERE account_id = %s ORDER BY trade_date, code", (aid,))


@app.get("/api/sim/accounts/{aid}/holdings")
def sim_holdings(aid: int):
    with db() as conn:
        return _query(conn,
                      "SELECT strategy_id, strategy_name, rebal_date, code, name, weight, "
                      "signal_close, raw_close, signal_equity, target_qty "
                      "FROM sim_holdings WHERE account_id = %s ORDER BY rebal_date, code", (aid,))


@app.get("/api/sim/accounts/{aid}/calendar")
def sim_calendar(aid: int):
    with db() as conn:
        latest = _one(conn, "SELECT max(trade_date) AS d FROM etf_bars_raw")["d"]
        rows = _query(conn,
                      "SELECT strategy_id, strategy_name, rebal_date, signal_date "
                      "FROM sim_calendar WHERE account_id = %s ORDER BY rebal_date, strategy_id", (aid,))
    for r in rows:
        r["is_future"] = latest is not None and r["rebal_date"] > latest
    return rows


# ================================================================ 5.6 任务
@app.get("/api/tasks")
def tasks():
    return list(TASKS.values())


@app.get("/api/tasks/{tid}")
def task(tid: str):
    t = TASKS.get(tid)
    if t is None:
        raise HTTPException(404, "任务不存在")
    return t


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)