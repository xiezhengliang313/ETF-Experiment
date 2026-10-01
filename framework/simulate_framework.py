# -*- coding: utf-8 -*-
"""模拟框架（唯一 class：SimulateFramework）

真实货币引擎：信号用后复权(hfq)复用回测框架的策略方法，成交/估值用不复权(raw)，
除息日从 etf_dividends 把现金分红计入现金。不复用回测框架的 engine/save。

账户化模型（v8）：
- sim_account = 一笔连续资金（账户级收益/风险指标 + init_cash + benchmark）
- sim_params  = 账户内的策略分段（同一笔钱沿时间线接续跑，换段不清仓）
- sim_nav     = 账户净值（账户级）；trades/holdings/calendar 带账户+策略双维度

sim_account.end_date 留空(NULL)时自动模拟到行情最新交易日，否则用显式截止日。

运行：python -m framework.simulate_framework
"""

import numpy as np
import pandas as pd
import psycopg

from .backtest_framework import BacktestFramework


class SimulateFramework:
    FEE = 0.0005
    SLIP = 0.0005   # 滑点(万五)：买成交价=真实收盘×(1+SLIP)，卖成交价=真实收盘×(1-SLIP)
    LOT = 100

    def __init__(self, fee=FEE, slip=SLIP, lot=LOT):
        self.bt = BacktestFramework()      # 只复用 trend/weights_*/compute_metrics/METRIC_COLS
        self.fee = float(fee)
        self.slip = float(slip)
        self.lot = int(lot)

    def connect(self):
        return self.bt.connect()

    # ---------------------------------------------------------------- 取数
    def load_raw(self, conn, codes, grid):
        if not grid:
            return pd.DataFrame()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT code, trade_date, close FROM etf_bars_raw "
                "WHERE code = ANY(%s) AND trade_date BETWEEN %s AND %s",
                (codes, grid[0], grid[-1]))
            rows = cur.fetchall()
        df = pd.DataFrame(rows, columns=["code", "trade_date", "close"])
        piv = df.pivot_table(index="trade_date", columns="code", values="close")
        piv = piv.reindex(pd.DatetimeIndex(grid)).sort_index()
        cols = [c for c in codes if c in piv.columns]
        return piv[cols].ffill()

    def load_dividends(self, conn, codes):
        with conn.cursor() as cur:
            cur.execute("SELECT code, ex_date, div_per_share FROM etf_dividends WHERE code = ANY(%s)",
                        (codes,))
            return {(r[0], r[1]): float(r[2]) for r in cur.fetchall()}

    # ---------------------------------------------------------------- 真实货币引擎（账户级）
    def engine(self, segments, start, end, init_cash, conn, state=False):
        """同一笔 init_cash 沿策略分段(按 start_date 已排序)连续跑，返回记录字典。

        segments: [{sid, name, method, params, codes, start, end}, ...]
        rec: {nav:[(d,nav,cash,mv,dividend,total)],
              trades:[(sid,sname,d,code,name,side,qty,price,amount,fee)],
              calendars:[(sid,sname,d,signal_date)],
              holdings:[(sid,sname,d,code,name,weight,signal_close,raw_close,signal_equity,target_qty)]}
        state=True 时额外返回末态（日期/现金/实际持股/生效分段/该日信号权重/收盘价），给「前瞻信号」用。
        """
        union = []
        for s in segments:
            for c in s["codes"]:
                if c not in union:
                    union.append(c)
        # 取数窗口 = 所有分段的最大 warmup
        warmup = 5
        for s in segments:
            p = s["params"]
            max_win = max(int(p.get("lookback") or 0), int(p.get("long_window") or 0))
            warmup = max(warmup, max_win + p["period"] + 5)
        C_hfq, name_map, grid = self.bt.load_prices(conn, union, start, end, warmup)
        C_raw = self.load_raw(conn, union, grid)
        divs = self.load_dividends(conn, union)
        all_cols = list(C_hfq.columns)
        if not all_cols:
            raise RuntimeError("成分无行情: " + ",".join(union))
        # 每个分段预备：本段成分列 + 权重函数
        for s in segments:
            s["seg_cols"] = [c for c in s["codes"] if c in all_cols]
            s["weight_fn"] = self.bt.make_signal(s["method"], s["params"])

        start_idx = next(i for i, d in enumerate(grid) if d >= start)
        cash = float(init_cash)
        qty = {c: 0 for c in all_cols}
        nav, trades, calendars, holdings = [], [], [], []
        cur_sid = None
        seg_k = 0
        for p in range(start_idx, len(grid)):
            d = grid[p]
            seg = self._active_segment(segments, d)
            # 1) 除息日现金分红入账（先分红后调仓；持仓=昨日收盘）
            dividend = 0.0
            for c in all_cols:
                dv = divs.get((c, d))
                if dv and qty[c]:
                    cash += qty[c] * dv
                    dividend += qty[c] * dv
            # 2) 调仓（当日生效分段才调仓；无分段的间隙日只持股估值）
            if seg is not None:
                if seg["sid"] != cur_sid:
                    cur_sid = seg["sid"]
                    seg_k = 0  # 换段：新段第一天必调仓（按新权重先卖后买）
                if seg_k % seg["params"]["period"] == 0:
                    sig = p - 1 if p > 0 else p
                    scols = seg["seg_cols"]
                    w = seg["weight_fn"](C_hfq[scols], sig)
                    raw_sig = C_raw.iloc[sig]
                    real_equity = cash + sum(qty[c] * raw_sig[c] for c in all_cols
                                             if not pd.isna(raw_sig[c]))
                    target = {}
                    for c in all_cols:
                        rc = raw_sig[c]
                        if pd.isna(rc) or rc <= 0 or c not in scols or c not in w:
                            target[c] = 0
                        else:
                            target[c] = int(np.floor(w[c] * real_equity / rc / self.lot) * self.lot)
                    ec = C_raw.iloc[p]
                    # 先卖后买（真实价）
                    for c in all_cols:
                        if target[c] < qty[c]:
                            sq = qty[c] - target[c]
                            px = ec[c] * (1 - self.slip)
                            if pd.isna(px) or px <= 0:
                                continue
                            amt = sq * px
                            fee = amt * self.fee
                            cash += amt - fee
                            qty[c] = target[c]
                            trades.append((seg["sid"], seg["name"], d, c, name_map.get(c),
                                           "SELL", sq, round(float(px), 4), amt, fee))
                    buys = [(c, target[c] - qty[c]) for c in all_cols if target[c] > qty[c]]
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
                            trades.append((seg["sid"], seg["name"], d, c, name_map.get(c),
                                           "BUY", bq2, round(float(px), 4), amt, fee))
                    calendars.append((seg["sid"], seg["name"], d, grid[sig]))
                    for c in scols:
                        if w.get(c) or target.get(c):
                            holdings.append((seg["sid"], seg["name"], d, c, name_map.get(c),
                                             float(w.get(c, 0.0)),
                                             float(C_hfq.iloc[sig][c]) if not pd.isna(C_hfq.iloc[sig][c]) else None,
                                             float(raw_sig[c]) if not pd.isna(raw_sig[c]) else None,
                                             real_equity, target[c]))
                seg_k += 1
            # 3) 估值（真实元）
            ec = C_raw.iloc[p]
            mv = sum(qty[c] * float(ec[c]) for c in all_cols if not pd.isna(ec[c]))
            total = cash + mv
            nav.append((d, total / float(init_cash), cash, mv, dividend, total))
        rec = {"nav": nav, "trades": trades, "calendars": calendars, "holdings": holdings}
        if state:
            # 末态 = 跑到的最后一天（= 最新真实行情日）。未来那次调仓的信号日若是这一天，
            # 调用方就能用「末态现金/实际持股 + 该日真实收盘价」算出计划持仓。
            last_p = len(grid) - 1
            hfq_last, raw_last = C_hfq.iloc[last_p], C_raw.iloc[last_p]
            w_last = seg["weight_fn"](C_hfq[seg["seg_cols"]], last_p) if seg is not None else {}
            rec["state"] = {
                "date": grid[last_p],
                "cash": cash,
                "qty": dict(qty),
                "seg": seg,          # 最后一天生效的分段（None = 那天没有分段在跑）
                "weight": {c: float(w_last.get(c, 0.0)) for c in all_cols},
                "hfq_close": {c: (None if pd.isna(hfq_last[c]) else float(hfq_last[c])) for c in all_cols},
                "raw_close": {c: (None if pd.isna(raw_last[c]) else float(raw_last[c])) for c in all_cols},
                "name": {c: name_map.get(c) for c in all_cols},
            }
        return rec

    def _active_segment(self, segments, d):
        for s in segments:
            if s["start"] <= d <= s["end"]:
                return s
        return None

    # ---------------------------------------------------------------- 基准（真实货币买入持有）
    def benchmark_nav(self, conn, bench_code, dates, init_cash):
        """首日满仓买基准 ETF（raw），持仓不动，除息日分红入现金。"""
        if not dates:
            return []
        cash0 = float(init_cash)
        raw_df = self.load_raw(conn, [bench_code], dates)
        if bench_code not in raw_df.columns:
            return []
        raw = raw_df[bench_code]
        divs = self.load_dividends(conn, [bench_code])
        cash, qty = cash0, 0
        rows = []
        for i, d in enumerate(dates):
            px = float(raw.iloc[i]) if not pd.isna(raw.iloc[i]) else 0.0
            if i == 0 and px > 0:
                qty = int(np.floor(cash / (px * (1 + self.slip) * (1 + self.fee)) / self.lot) * self.lot)
                amt = qty * px * (1 + self.slip)
                cash -= amt + amt * self.fee
            dv = divs.get((bench_code, d))
            if dv and qty:
                cash += qty * dv
            total = cash + qty * px
            rows.append((d, total / cash0))
        return rows

    # ---------------------------------------------------------------- 落库
    def save(self, conn, account_id, account_name, rec, since=None):
        def after(x):
            return since is None or x > since
        nav_rows = [(account_id, account_name, d, round(n, 6), c, mv, dv, t)
                    for d, n, c, mv, dv, t in rec["nav"] if after(d)]
        trade_rows = [(account_id, account_name, sid, sname, d, c, nm, s, q, p, a, round(f, 2))
                      for sid, sname, d, c, nm, s, q, p, a, f in rec["trades"] if after(d)]
        cal_rows = [(account_id, account_name, sid, sname, d, sd)
                    for sid, sname, d, sd in rec["calendars"] if after(d)]
        hold_rows = [(account_id, account_name, sid, sname, d, c, nm, w, sc, rc, se, tq)
                     for sid, sname, d, c, nm, w, sc, rc, se, tq in rec["holdings"] if after(d)]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO sim_nav (account_id, account_name, trade_date, nav, cash, market_value, dividend, total) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", nav_rows)
            cur.executemany(
                "INSERT INTO sim_trades (account_id, account_name, strategy_id, strategy_name, trade_date, code, name, side, qty, price, amount, fee) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", trade_rows)
            cur.executemany(
                "INSERT INTO sim_calendar (account_id, account_name, strategy_id, strategy_name, rebal_date, signal_date) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", cal_rows)
            cur.executemany(
                "INSERT INTO sim_holdings (account_id, account_name, strategy_id, strategy_name, rebal_date, code, name, weight, signal_close, raw_close, signal_equity, target_qty) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", hold_rows)
        conn.commit()

    def save_benchmark_nav(self, conn, account_id, account_name, bench_code, bench_rows, since=None):
        rows = [(account_id, account_name, bench_code, d, round(n, 6))
                for d, n in bench_rows if since is None or d > since]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO sim_benchmark_nav (account_id, account_name, benchmark, trade_date, nav) "
                "VALUES (%s,%s,%s,%s,%s)", rows)
        conn.commit()

    def update_metrics(self, conn, account_id, m):
        cols = self.bt.METRIC_COLS
        sets = ", ".join(f"{c} = %s" for c in cols)
        vals = [m.get(c) for c in cols]
        with conn.cursor() as cur:
            cur.execute(f"UPDATE sim_account SET {sets}, metrics_at = now() WHERE account_id = %s",
                        vals + [account_id])
        conn.commit()

    # ------------------------------------------------------- 前瞻信号（未来那次调仓）
    def future_signal_rows(self, rec, fut, sid=None):
        """下一调仓日 fut=(rebal_date, signal_date) 的「计划持仓」行，算不出来返回 []。

        与回测侧同一套口径，只是股数用**真实价**推（模拟盘是真实货币）：
        目标股数 = floor(权重 × 信号日真实权益 ÷ 信号日真实收盘价 ÷ 100) × 100。
        前提：信号日收盘价已到手（= 引擎跑到的最后一天）、且那天有生效分段；
        另外只认「信号日仍在生效的那个分段」，避免换段前的老分段碰巧同一天发信号时错配。
        """
        st = rec.get("state")
        if not st or st["date"] != fut[1] or st["seg"] is None:
            return []
        seg = st["seg"]
        if sid is not None and seg["sid"] != sid:
            return []
        raw, hfq = st["raw_close"], st["hfq_close"]
        equity = st["cash"] + sum(st["qty"][c] * raw[c] for c in st["qty"] if raw.get(c))
        rows = []
        for c, held in st["qty"].items():
            w = st["weight"].get(c, 0.0)
            p = raw.get(c)
            tq = 0
            if w > 0 and p and p > 0:
                tq = int(np.floor(w * equity / p / self.lot) * self.lot)
            if w > 0 or held > 0:
                rows.append((seg["sid"], seg["name"], fut[0], c, st["name"].get(c), w,
                             hfq.get(c), p, equity, tq))
        return rows

    def save_future_holdings(self, conn, account_id, account_name, rebal_date, rows):
        """写「下一调仓日」的计划持仓：先删该账户该日的旧行（重跑幂等），再插。"""
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sim_holdings WHERE account_id = %s AND rebal_date = %s",
                        (account_id, rebal_date))
            if rows:
                cur.executemany(
                    "INSERT INTO sim_holdings (account_id, account_name, strategy_id, strategy_name, rebal_date, code, name, weight, signal_close, raw_close, signal_equity, target_qty) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(account_id, account_name, *r) for r in rows])
        conn.commit()
        return len(rows)

    # ---------------------------------------------------------------- 查询辅助
    def _load_accounts(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT account_id, account_name, init_cash, start_date, end_date, benchmark "
                        "FROM sim_account WHERE is_active = TRUE ORDER BY account_id")
            return cur.fetchall()

    def _load_segments(self, conn, account_id):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT strategy_id, strategy_name, strategy_method, period, lookback, min_w, max_w, "
                "long_window, short_window, codes, start_date, end_date "
                "FROM sim_params WHERE account_id = %s "
                "ORDER BY start_date, strategy_id", (account_id,))
            rows = cur.fetchall()
        segs = []
        for sid, sname, method, period, lookback, min_w, max_w, lw, sw, codes, s, e in rows:
            params = ({"period": period, "long_window": lw, "short_window": sw}
                      if method == "trend" else
                      {"period": period, "lookback": lookback,
                       "min_w": float(min_w or 0), "max_w": float(max_w or 1)})
            segs.append({"sid": sid, "name": sname, "method": method, "params": params,
                         "codes": list(codes), "start": s, "end": e})
        return segs

    def _last_nav_date(self, conn, account_id):
        with conn.cursor() as cur:
            cur.execute("SELECT max(trade_date) FROM sim_nav WHERE account_id = %s", (account_id,))
            return cur.fetchone()[0]

    def _latest_trade_date(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT max(trade_date) FROM etf_bars_raw")
            return cur.fetchone()[0]

    def schedule_future_calendar(self, conn, account_id, account_name, sid, sname, period):
        """把该分段的下一未来调仓日写进 sim_calendar（rebal_date > 最新真实行情日 = 未来）。"""
        with conn.cursor() as cur:
            cur.execute("SELECT max(trade_date) FROM etf_bars_raw")
            latest = cur.fetchone()[0]
            cur.execute(
                "SELECT max(rebal_date) FROM sim_calendar "
                "WHERE account_id = %s AND strategy_id = %s AND rebal_date <= %s",
                (account_id, sid, latest))
            last = cur.fetchone()[0]
            cur.execute(
                "DELETE FROM sim_calendar WHERE account_id = %s AND strategy_id = %s AND rebal_date > %s",
                (account_id, sid, latest))
        if last is None:
            return None
        fut = self.bt.next_rebalance(conn, period, last)
        while fut is not None and fut[0] <= latest:
            fut = self.bt.next_rebalance(conn, period, fut[0])
        if fut is not None:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO sim_calendar (account_id, account_name, strategy_id, strategy_name, rebal_date, signal_date) "
                    "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (account_id, account_name, sid, sname, fut[0], fut[1]))
            conn.commit()
        return fut

    # ---------------------------------------------------------------- 主流程
    def run(self):
        conn = self.connect()
        try:
            accounts = self._load_accounts(conn)
            if not accounts:
                print("[SIM] sim_account 无有效账户，不执行")
                return
            latest = None
            for account_id, account_name, init_cash, start, end, bench in accounts:
                if end is None:
                    if latest is None:
                        latest = self._latest_trade_date(conn)
                    end = latest
                segments = self._load_segments(conn, account_id)
                if not segments:
                    print(f"[SIM] 账户 {account_id}（{account_name}）无有效策略分段，跳过")
                    continue
                last = self._last_nav_date(conn, account_id)
                rec = self.engine(segments, start, end, float(init_cash), conn, state=True)
                dates = [d for d, *_ in rec["nav"]]
                m = self.bt.compute_metrics([x[1] for x in rec["nav"]])
                bm_rows = self.benchmark_nav(conn, bench, dates, float(init_cash))
                bm = self.bt.compute_metrics([x[1] for x in bm_rows])
                for k, v in bm.items():
                    m["bm_" + k] = v
                m["excess_total"] = (m.get("ret_total") - m.get("bm_ret_total")) \
                    if m.get("ret_total") is not None and m.get("bm_ret_total") is not None else None
                m["excess_annual"] = (m.get("ret_annual") - m.get("bm_ret_annual")) \
                    if m.get("ret_annual") is not None and m.get("bm_ret_annual") is not None else None
                self.save(conn, account_id, account_name, rec, None if last is None else last)
                self.save_benchmark_nav(conn, account_id, account_name, bench, bm_rows,
                                        None if last is None else last)
                self.update_metrics(conn, account_id, m)
                for seg in segments:
                    fut = self.schedule_future_calendar(conn, account_id, account_name,
                                                        seg["sid"], seg["name"], seg["params"]["period"])
                    if fut is not None:
                        # 信号日已到手（= 最新行情日）且那天正是这个分段在跑 → 算未来那次的计划持仓
                        n = self.save_future_holdings(
                            conn, account_id, account_name, fut[0],
                            self.future_signal_rows(rec, fut, sid=seg["sid"]))
                        if n:
                            print(f"[SIM]   ↳ 前瞻信号：信号日 {fut[1]} → 调仓日 {fut[0]}"
                                  f"（{seg['name']}），{n} 只")
                tag = "FULL" if last is None else "INCR"
                ra = m.get("ret_annual")
                ra_s = f"{ra:.4f}" if ra is not None else "N/A"
                print(f"[SIM] account_id={account_id} 账户={account_name} "
                      f"init_cash={float(init_cash):.0f} nav_end={m['nav_end']:.4f} "
                      f"ret_annual={ra_s} mdd={m['max_drawdown']:.4f} [{tag}]")
        finally:
            conn.close()


# ================================================================ 改名同步
def sync_segment_name(conn, strategy_id, strategy_name):
    """模拟分段改名后，把 3 张结果表里冗余的 strategy_name 刷成新名。

    真源是 sim_params（strategy_id = 分段编码），结果表里那份只是免 JOIN 展示用。
    凡是会改 sim_params.strategy_name 的地方都必须调用本函数。
    调用点：PUT /api/sim/accounts/{aid}/segments/{sid}。
    返回实际更新的总行数。
    """
    n = 0
    with conn.cursor() as cur:
        for tbl in ("sim_trades", "sim_holdings", "sim_calendar"):
            cur.execute(f"UPDATE {tbl} SET strategy_name = %s "
                        f"WHERE strategy_id = %s AND strategy_name IS DISTINCT FROM %s",
                        (strategy_name, strategy_id, strategy_name))
            n += cur.rowcount
    conn.commit()
    return n


if __name__ == "__main__":
    SimulateFramework().run()