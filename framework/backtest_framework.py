# -*- coding: utf-8 -*-
"""回测框架（唯一 class：BacktestFramework）

纯后复权(hfq)引擎 + 策略方法 + 指标/基准回填 + 首次全量/再次增量。

策略方法做成"显式传价格矩阵"的纯方法，供寻优框架复用完整引擎、
供模拟框架复用 trend/weights_* 方法（两份都不调用本类的 save()）。
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
from scipy.optimize import minimize


class BacktestFramework:
    CASH0 = 1_000_000.0
    FEE = 0.0005
    SLIP = 0.0005   # 滑点(万五)：买成交价=收盘×(1+SLIP)，卖成交价=收盘×(1-SLIP)
    LOT = 100
    # 指标窗口（交易日数）
    RET_WINDOWS = {
        "ret_1d": 1, "ret_1w": 5, "ret_1m": 21,
        "ret_3m": 63, "ret_1y": 252, "ret_2y": 504, "ret_3y": 756,
    }
    METRIC_COLS = [
        "nav_end", "ret_total", "ret_annual",
        "ret_1d", "ret_1w", "ret_1m", "ret_3m", "ret_1y", "ret_2y", "ret_3y",
        "max_drawdown", "sharpe", "annual_vol", "calmar", "sortino",
        # 基准块与上面策略块**逐项一一对应**（同顺序、同 15 个），便于前端并排对齐比较
        "bm_nav_end", "bm_ret_total", "bm_ret_annual",
        "bm_ret_1d", "bm_ret_1w", "bm_ret_1m", "bm_ret_3m", "bm_ret_1y", "bm_ret_2y", "bm_ret_3y",
        "bm_max_drawdown", "bm_sharpe", "bm_annual_vol", "bm_calmar", "bm_sortino",
        "excess_total", "excess_annual",
    ]

    def __init__(self, cash=CASH0, fee=FEE, slip=SLIP, lot=LOT):
        self.cash0 = float(cash)
        self.fee = float(fee)
        self.slip = float(slip)
        self.lot = int(lot)

    # ---------------------------------------------------------------- 连接
    def connect(self):
        env = Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        return psycopg.connect(
            host=os.environ.get("PG_HOST", "localhost"),
            port=os.environ.get("PG_PORT", "5432"),
            dbname=os.environ.get("PG_DB", "etf"),
            user=os.environ.get("PG_USER", "postgres"),
            password=os.environ.get("PG_PASSWORD", ""),
        )

    # ------------------------------------------------------------ 策略方法（纯方法）
    def trend(self, logprices):
        """对数价的年化趋势值 = 斜率 * R^2 * 252。"""
        y = np.asarray(logprices, dtype=float)
        n = len(y)
        if n < 2 or np.isnan(y).any():
            return 0.0
        x = np.arange(n, dtype=float)
        slope, intercept = np.polyfit(x, y, 1)
        yhat = slope * x + intercept
        ss_res = float(np.sum((y - yhat) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        return float(slope * r2 * 252.0)

    def _window(self, C, idx, lookback):
        """回看窗口内的 (年化收益均值, 年化协方差, 有效代码列表)。"""
        P = C.iloc[idx - lookback: idx + 1]
        R = P.pct_change().iloc[1:]
        valid = [c for c in C.columns if not R[c].isna().any()]
        if not valid:
            return None, None, []
        R = R[valid]
        mu = R.mean().to_numpy() * 252.0
        S = R.cov().to_numpy() * 252.0
        return mu, S, valid

    @staticmethod
    def _ridge(S):
        S = np.asarray(S, dtype=float)
        n = S.shape[0]
        return S + np.eye(n) * (np.trace(S) / n) * 1e-6

    @staticmethod
    def _optimize(objective, x0, bounds):
        cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
        res = minimize(objective, x0, method="SLSQP", bounds=bounds, constraints=cons)
        w = np.clip(res.x, bounds[0][0], bounds[0][1])
        s = w.sum()
        return w / s if s > 0 else w

    def weights_max_sharpe(self, C, idx, lookback, min_w, max_w):
        mu, S, codes = self._window(C, idx, lookback)
        if not codes:
            return {}
        S = self._ridge(S)
        n = len(codes)
        x0 = np.full(n, 1.0 / n)
        bnd = [(min_w, max_w)] * n

        def neg_sharpe(w):
            d = float(np.sqrt(w @ S @ w))
            return -(float(w @ mu) / d) if d > 0 else 1e12

        w = self._optimize(neg_sharpe, x0, bnd)
        return dict(zip(codes, w))

    def weights_min_variance(self, C, idx, lookback, min_w, max_w):
        _, S, codes = self._window(C, idx, lookback)
        if not codes:
            return {}
        S = self._ridge(S)
        n = len(codes)
        x0 = np.full(n, 1.0 / n)
        bnd = [(min_w, max_w)] * n
        w = self._optimize(lambda w: float(w @ S @ w), x0, bnd)
        return dict(zip(codes, w))

    def weights_trend(self, C, idx, long_window, short_window):
        best_code, best_val = None, -np.inf
        for c in C.columns:
            seg = C[c].iloc[idx - long_window + 1: idx + 1]
            if seg.isna().any() or len(seg) < long_window:
                continue
            lp = np.log(seg.astype(float))
            lt = self.trend(lp.to_numpy())
            st = self.trend(lp.iloc[-short_window:].to_numpy())
            val = st - lt
            if val > best_val:
                best_val, best_code = val, c
        return {best_code: 1.0} if best_code else {}

    def make_signal(self, strategy_method, params):
        if strategy_method == "trend":
            lw, sw = params["long_window"], params["short_window"]
            return lambda C, idx: self.weights_trend(C, idx, lw, sw)
        lb, mn, mx = params["lookback"], params["min_w"], params["max_w"]
        if strategy_method == "max_sharpe":
            return lambda C, idx: self.weights_max_sharpe(C, idx, lb, mn, mx)
        if strategy_method == "min_variance":
            return lambda C, idx: self.weights_min_variance(C, idx, lb, mn, mx)
        raise RuntimeError("unknown strategy_method: " + str(strategy_method))

    # ---------------------------------------------------------------- 取数
    def load_prices(self, conn, codes, start, end, warmup=0):
        """返回 (close 矩阵 DataFrame[grid 升序, codes], name 映射, grid 列表)。"""
        with conn.cursor() as cur:
            pre = []
            if warmup:
                cur.execute(
                    "SELECT cal_date FROM trade_calendar WHERE cal_date < %s AND is_open "
                    "ORDER BY cal_date DESC LIMIT %s", (start, warmup))
                pre = [r[0] for r in cur.fetchall()][::-1]
            cur.execute(
                "SELECT cal_date FROM trade_calendar WHERE cal_date BETWEEN %s AND %s AND is_open "
                "ORDER BY cal_date", (start, end))
            main = [r[0] for r in cur.fetchall()]
        grid = sorted(set(pre + main))
        if not grid:
            raise RuntimeError("区间内无交易日")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT code, trade_date, close, name FROM etf_bars_hfq "
                "WHERE code = ANY(%s) AND trade_date BETWEEN %s AND %s",
                (codes, grid[0], grid[-1]))
            rows = cur.fetchall()
        df = pd.DataFrame(rows, columns=["code", "trade_date", "close", "name"])
        name_map = df.groupby("code")["name"].first().to_dict()
        piv = df.pivot_table(index="trade_date", columns="code", values="close")
        piv = piv.reindex(pd.DatetimeIndex(grid)).sort_index()
        cols = [c for c in codes if c in piv.columns]
        return piv[cols].ffill(), name_map, grid

    # ---------------------------------------------------------------- 后复权引擎
    def engine(self, strategy_method, params, codes, start, end, conn, data=None, state=False):
        # data=(C, name_map, grid) 由调用方（寻优框架）预载一次传入；否则自己按需加载
        # state=True 时额外返回末态（现金/持仓/收盘/该日信号权重），给「前瞻信号」用；
        # 寻优框架每个候选都要跑一次引擎，不需要末态，所以默认 False 省掉一次权重计算。
        period = params["period"]
        if data is None:
            max_win = max(int(params.get("lookback") or 0), int(params.get("long_window") or 0))
            C, name_map, grid = self.load_prices(conn, codes, start, end, max_win + period + 5)
        else:
            C, name_map, grid = data
        cols = list(C.columns)
        if not cols:
            raise RuntimeError("成分无 hfq 数据: " + ",".join(codes))

        # grid 中 >= start 的位置即"主区间"
        start_idx = next(i for i, d in enumerate(grid) if d >= start)
        main_pos = list(range(start_idx, len(grid)))

        weight_fn = self.make_signal(strategy_method, params)
        cash = self.cash0
        qty = {c: 0 for c in cols}

        nav, trades, calendars, holdings = [], [], [], []
        for k, p in enumerate(main_pos):
            d = grid[p]
            if k % period == 0:
                sig = p - 1 if p > 0 else p
                sc = C.iloc[sig]
                sig_equity = cash + sum(
                    qty[c] * sc[c] for c in cols if not pd.isna(sc[c]))
                w = weight_fn(C, sig)
                target = {}
                for c in cols:
                    px = sc[c]
                    if pd.isna(px) or px <= 0 or c not in w:
                        target[c] = 0
                    else:
                        target[c] = int(np.floor(w[c] * sig_equity / px / self.lot) * self.lot)
                ec = C.iloc[p]
                # 先卖后买
                for c in cols:
                    if target[c] < qty[c]:
                        sq = qty[c] - target[c]
                        px = ec[c] * (1 - self.slip)
                        if pd.isna(px) or px <= 0:
                            continue
                        amt = sq * px
                        fee = amt * self.fee
                        cash += amt - fee
                        qty[c] = target[c]
                        trades.append((d, c, name_map.get(c), "SELL", sq, round(float(px), 4), amt, fee))
                buys = [(c, target[c] - qty[c]) for c in cols if target[c] > qty[c]]
                if buys:
                    cost = sum(bq * float(ec[c]) * (1 + self.slip) * (1 + self.fee)
                               for c, bq in buys if not pd.isna(ec[c]) and ec[c] > 0)
                    scale = min(1.0, cash / cost) if cost > 0 else 1.0
                    for c, bq in buys:
                        px = ec[c] * (1 + self.slip)
                        if pd.isna(px) or px <= 0:
                            continue
                        bq2 = int(np.floor(bq * scale / self.lot) * self.lot)
                        if bq2 <= 0:
                            continue
                        amt = bq2 * px
                        fee = amt * self.fee
                        cash -= amt + fee
                        qty[c] += bq2
                        trades.append((d, c, name_map.get(c), "BUY", bq2, round(float(px), 4), amt, fee))
                calendars.append((d, grid[sig]))
                for c in cols:
                    hold_w = float(w.get(c, 0.0))
                    hold_qty = target.get(c, 0)
                    if hold_w > 0 or hold_qty > 0:
                        holdings.append((d, c, name_map.get(c), hold_w,
                                         float(sc[c]) if not pd.isna(sc[c]) else None,
                                         sig_equity, hold_qty))
            ec = C.iloc[p]
            mv = sum(qty[c] * float(ec[c]) for c in cols if not pd.isna(ec[c]))
            total = cash + mv
            nav.append((d, total / self.cash0, cash, mv, total))
        rec = {"nav": nav, "trades": trades, "calendars": calendars, "holdings": holdings}
        if state:
            # 末态 = 引擎跑到的最后一天（= 最新行情日）。若「下一调仓日」的信号日正是这一天，
            # 调用方就能用这一天的收盘价直接算出未来那次调仓的目标持仓（引擎本身只跑已发生的调仓）。
            last_p = main_pos[-1]
            close_last = C.iloc[last_p]
            w_last = weight_fn(C, last_p)
            rec["state"] = {
                "date": grid[last_p],
                "cash": cash,
                "qty": dict(qty),
                "close": {c: (None if pd.isna(close_last[c]) else float(close_last[c])) for c in cols},
                "weight": {c: float(w_last.get(c, 0.0)) for c in cols},
                "name": {c: name_map.get(c) for c in cols},
            }
        return rec

    # ---------------------------------------------------------------- 指标
    def compute_metrics(self, nav):
        nav = np.asarray(nav, dtype=float)
        nav = nav[np.isfinite(nav)]
        if len(nav) == 0:
            return {}
        nav_end = float(nav[-1])
        ret_total = nav_end - 1.0
        n = len(nav)
        years = n / 252.0
        ret_annual = float(nav_end ** (1.0 / years) - 1.0) if nav_end > 0 and years > 0 else None
        m = {"nav_end": nav_end, "ret_total": ret_total, "ret_annual": ret_annual}
        for k, w in self.RET_WINDOWS.items():
            m[k] = float(nav[-1] / nav[-1 - w] - 1.0) if w < n else None
        peak = np.maximum.accumulate(nav)
        # 回撤用负号口径：0 = 创新高，-0.2687 = 最大回撤 26.87%
        m["max_drawdown"] = float(((nav - peak) / peak).min()) if n > 0 else 0.0
        r = np.diff(nav) / nav[:-1]
        r = r[np.isfinite(r)]
        if len(r) > 1:
            sd = float(r.std(ddof=1))
            m["annual_vol"] = sd * np.sqrt(252.0)
            m["sharpe"] = float(r.mean() / sd * np.sqrt(252.0)) if sd > 0 else None
            # 索提诺 = 年化 / 年化下行波动：负收益算离差、正收益记 0，分母仍取**全部**交易日
            # （教科书的目标下行偏差），ddof=1 与上面的夏普/年化波动同口径。
            downside = float(np.sqrt((np.minimum(r, 0.0) ** 2).sum() / (len(r) - 1)) * np.sqrt(252.0))
            m["sortino"] = float(ret_annual / downside) if (ret_annual is not None and downside > 0) else None
        else:
            m["annual_vol"] = m["sharpe"] = m["sortino"] = None
        # 卡玛 = 年化 / |最大回撤|（回撤存负数）。回撤为 0 或年化缺失时记 0.0。
        # 这里是唯一算法（寻优的综合分直接取这个入库值打分，不再自己重算）。
        d_mdd = abs(m["max_drawdown"] or 0.0)
        m["calmar"] = float((ret_annual or 0.0) / d_mdd) if d_mdd > 0 else 0.0
        return m

    def benchmark_nav(self, conn, bench_code, dates):
        """后复权买入持有基准净值（起点 1.0），dates 为交易日列表。"""
        if not dates:
            return []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trade_date, close FROM etf_bars_hfq "
                "WHERE code = %s AND trade_date BETWEEN %s AND %s ORDER BY trade_date",
                (bench_code, dates[0], dates[-1]))
            rows = cur.fetchall()
        s = pd.Series([r[1] for r in rows],
                      index=pd.DatetimeIndex([r[0] for r in rows]),
                      dtype=float)
        s = s.reindex(pd.DatetimeIndex(dates)).ffill()
        if s.isna().all():
            return []
        base = float(s.iloc[0])
        return [(d, float(s.iloc[i] / base)) for i, d in enumerate(dates)]

    # ------------------------------------------------------------ 未来调仓日程
    def next_rebalance(self, conn, period, after_date):
        """after_date 之后第 period 个交易日 = 下一调仓日；返回 (rebal_date, signal_date) 或 None。"""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cal_date FROM trade_calendar WHERE is_open AND cal_date > %s "
                "ORDER BY cal_date", (after_date,))
            days = [r[0] for r in cur.fetchall()]
        if len(days) < period:
            return None
        rebal = days[period - 1]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cal_date FROM trade_calendar WHERE is_open AND cal_date < %s "
                "ORDER BY cal_date DESC LIMIT 1", (rebal,))
            sig = cur.fetchone()
        return (rebal, sig[0] if sig else rebal)

    def schedule_future_calendar(self, conn, strategy_id, strategy_name, period):
        """把下一未来调仓日(含信号日)写进 bt_calendar（rebal_date > 最新行情日 = 未来）。"""
        with conn.cursor() as cur:
            cur.execute("SELECT max(trade_date) FROM etf_bars_hfq")
            latest = cur.fetchone()[0]
            cur.execute(
                "SELECT max(rebal_date) FROM bt_calendar WHERE strategy_id = %s AND rebal_date <= %s",
                (strategy_id, latest))
            last = cur.fetchone()[0]
            cur.execute("DELETE FROM bt_calendar WHERE strategy_id = %s AND rebal_date > %s",
                        (strategy_id, latest))
        if last is None:
            return None
        fut = self.next_rebalance(conn, period, last)
        while fut is not None and fut[0] <= latest:
            fut = self.next_rebalance(conn, period, fut[0])
        if fut is not None:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO bt_calendar (strategy_id, strategy_name, rebal_date, signal_date) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (strategy_id, strategy_name, fut[0], fut[1]))
            conn.commit()
        return fut

    # ------------------------------------------------------- 前瞻信号（未来那次调仓）
    def future_signal_rows(self, rec, fut):
        """下一调仓日 fut=(rebal_date, signal_date) 的「计划持仓」行，算不出来就返回 []。

        引擎只跑「已发生」的调仓，所以未来这一行要单独算：拿信号日（= 调仓日前一交易日）
        收盘价跑一遍同一个权重函数，再按「信号日权益 = 现金 + 持仓市值」算目标股数，
        规则与引擎内完全一致（目标股数 = floor(w × 权益 / 收盘价 / 手数) × 手数）。
        前提是信号日的收盘价已到手 —— 也就是引擎跑到的最后一天恰好等于信号日。
        权重为 0 但当前还持有的成分也保留（目标股数 0），这样页面能看出这次要清仓。
        """
        st = rec.get("state")
        if not st or st["date"] != fut[1]:
            return []
        px = st["close"]
        equity = st["cash"] + sum(
            st["qty"][c] * px[c] for c in st["qty"] if px.get(c) is not None)
        rows = []
        for c, held in st["qty"].items():
            w = st["weight"].get(c, 0.0)
            p = px.get(c)
            tq = 0
            if w > 0 and p and p > 0:
                tq = int(np.floor(w * equity / p / self.lot) * self.lot)
            if w > 0 or held > 0:
                rows.append((fut[0], c, st["name"].get(c), w, p, equity, tq))
        return rows

    def save_future_holdings(self, conn, strategy_id, strategy_name, rebal_date, rows):
        """写「下一调仓日」的计划持仓：先删该策略该日的旧行（重跑幂等），再插。"""
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bt_holdings WHERE strategy_id = %s AND rebal_date = %s",
                        (strategy_id, rebal_date))
            if rows:
                cur.executemany(
                    "INSERT INTO bt_holdings (strategy_id, strategy_name, rebal_date, code, name, weight, signal_close, signal_equity, target_qty) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(strategy_id, strategy_name, *r) for r in rows])
        conn.commit()
        return len(rows)

    # ---------------------------------------------------------------- 落库
    def save(self, conn, strategy_id, strategy_name, rec, since=None):
        def after(x):
            return since is None or x > since
        nav_rows = [(strategy_id, strategy_name, d, round(n, 6), c, mv, t)
                    for d, n, c, mv, t in rec["nav"] if after(d)]
        trade_rows = [(strategy_id, strategy_name, d, c, nm, s, q, p, a, round(f, 2))
                      for d, c, nm, s, q, p, a, f in rec["trades"] if after(d)]
        cal_rows = [(strategy_id, strategy_name, d, sd)
                    for d, sd in rec["calendars"] if after(d)]
        hold_rows = [(strategy_id, strategy_name, d, c, nm, w, sc, se, tq)
                     for d, c, nm, w, sc, se, tq in rec["holdings"] if after(d)]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO bt_nav (strategy_id, strategy_name, trade_date, nav, cash, market_value, total) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)", nav_rows)
            cur.executemany(
                "INSERT INTO bt_trades (strategy_id, strategy_name, trade_date, code, name, side, qty, price, amount, fee) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", trade_rows)
            cur.executemany(
                "INSERT INTO bt_calendar (strategy_id, strategy_name, rebal_date, signal_date) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", cal_rows)
            cur.executemany(
                "INSERT INTO bt_holdings (strategy_id, strategy_name, rebal_date, code, name, weight, signal_close, signal_equity, target_qty) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", hold_rows)
        conn.commit()

    def save_benchmark_nav(self, conn, strategy_id, bench_code, bench_rows, since=None):
        rows = [(strategy_id, bench_code, d, round(n, 6))
                for d, n in bench_rows if since is None or d > since]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO bt_benchmark_nav (strategy_id, benchmark, trade_date, nav) "
                "VALUES (%s,%s,%s,%s)", rows)
        conn.commit()

    def update_metrics(self, conn, strategy_id, m):
        sets = ", ".join(f"{c} = %s" for c in self.METRIC_COLS)
        vals = [m.get(c) for c in self.METRIC_COLS]
        with conn.cursor() as cur:
            cur.execute(f"UPDATE bt_params SET {sets}, metrics_at = now() WHERE strategy_id = %s",
                        vals + [strategy_id])
        conn.commit()

    def _last_nav_date(self, conn, strategy_id):
        with conn.cursor() as cur:
            cur.execute("SELECT max(trade_date) FROM bt_nav WHERE strategy_id = %s", (strategy_id,))
            return cur.fetchone()[0]

    def _latest_trade_date(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT max(trade_date) FROM etf_bars_raw")
            return cur.fetchone()[0]

    # ---------------------------------------------------------------- 主流程
    def run(self):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT strategy_id, strategy_name, strategy_method, period, lookback, min_w, max_w, "
                    "long_window, short_window, codes, start_date, end_date, benchmark "
                    "FROM bt_params WHERE is_active = TRUE ORDER BY strategy_id")
                rows = cur.fetchall()
            if not rows:
                print("[BT] bt_params 无有效策略，不执行")
                return
            latest = None
            for r in rows:
                (sid, sname, method, period, lookback, min_w, max_w,
                 long_window, short_window, codes, start, end, bench) = r
                if end is None:
                    # end_date 留空 = 跟最新行情（只取一次，本次运行内所有策略保持一致）
                    if latest is None:
                        latest = self._latest_trade_date(conn)
                    end = latest
                params = ({"period": period, "long_window": long_window, "short_window": short_window}
                          if method == "trend" else
                          {"period": period, "lookback": lookback, "min_w": float(min_w or 0), "max_w": float(max_w or 1)})
                last = self._last_nav_date(conn, sid)
                rec = self.engine(method, params, list(codes), start, end, conn, state=True)
                dates = [d for d, *_ in rec["nav"]]
                m = self.compute_metrics([x[1] for x in rec["nav"]])
                bm = self.compute_metrics([x[1] for x in self.benchmark_nav(conn, bench, dates)])
                for k, v in bm.items():
                    m["bm_" + k] = v
                m["excess_total"] = (m.get("ret_total") - m.get("bm_ret_total")) if m.get("ret_total") is not None and m.get("bm_ret_total") is not None else None
                m["excess_annual"] = (m.get("ret_annual") - m.get("bm_ret_annual")) if m.get("ret_annual") is not None and m.get("bm_ret_annual") is not None else None
                self.save(conn, sid, sname, rec, None if last is None else last)
                self.save_benchmark_nav(conn, sid, bench, self.benchmark_nav(conn, bench, dates),
                                         None if last is None else last)
                self.update_metrics(conn, sid, m)
                fut = self.schedule_future_calendar(conn, sid, sname, period)
                if fut is not None:
                    # 信号日已到手（= 最新行情日）时，把"下一调仓日买什么"也算出来存进去
                    n = self.save_future_holdings(conn, sid, sname, fut[0], self.future_signal_rows(rec, fut))
                    if n:
                        print(f"[BT]   ↳ 前瞻信号：信号日 {fut[1]} → 调仓日 {fut[0]}，{n} 只")
                tag = "FULL" if last is None else "INCR"
                print(f"[BT] strategy_id={sid} method={method} nav_end={m['nav_end']:.4f} "
                      f"ret_annual={m['ret_annual']:.4f} mdd={m['max_drawdown']:.4f} [{tag}]")
        finally:
            conn.close()


# ================================================================ 改名同步
def sync_strategy_name(conn, strategy_id, strategy_name):
    """策略改名后，把 4 张结果表里冗余的 strategy_name 刷成新名。

    结果表的 strategy_name 只是「免 JOIN 展示」的副本，唯一真源是 bt_params。
    凡是会改 bt_params.strategy_name 的地方都必须调用本函数，否则历史行会
    永远留着旧名（曾残留过 "行业轮动-最小方差-annual,calmar,sharpe" 这类寻优旧后缀）。
    调用点：PUT /api/backtest/strategies/{sid}、寻优晋级 OptimizeFramework.push_best()。
    返回实际更新的总行数。
    """
    n = 0
    with conn.cursor() as cur:
        for tbl in ("bt_nav", "bt_trades", "bt_holdings", "bt_calendar"):
            cur.execute(f"UPDATE {tbl} SET strategy_name = %s "
                        f"WHERE strategy_id = %s AND strategy_name IS DISTINCT FROM %s",
                        (strategy_name, strategy_id, strategy_name))
            n += cur.rowcount
    conn.commit()
    return n


if __name__ == "__main__":
    BacktestFramework().run()