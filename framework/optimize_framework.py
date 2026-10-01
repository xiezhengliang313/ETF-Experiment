# -*- coding: utf-8 -*-
"""寻优框架（唯一 class：OptimizeFramework）

方法：**滚动窗口 + 样本外验证（walk-forward）**。

对 bt_params 里 is_active=FALSE 的策略，按它的 method + codes + 起止日期，把整段交易日切成
若干「训练窗（样本内）→ 紧接的样本外窗」：

    |——训练窗——|—样本外—|
          |——训练窗——|—样本外—|
                |——训练窗——|—样本外—|

每折：在训练窗里跑完整个参数网格（复用回测框架的完整引擎，hfq 口径，全程不调 backtest.save()），
按**综合分**选出该折最优参数，再让它在紧接的样本外窗里跑一遍。各折样本外段**首尾相接**
拼成一条样本外净值 → 在它上面算出的年化/回撤/夏普等才是**诚实的样本外成绩**（不受参数挑选污染）。

晋级参数用**参数级稳定性投票**定：每折取训练窗综合分前 TOP_PCT（缺省 10%）的候选，**逐参数**统计
「有多少折的前 K 里出现过该取值」（入选折数），各参数取入选折数最多的取值拼成稳定组合，再投影到
真实候选里逐项匹配最多的那一套 —— 保证晋级参数一定是历史上真实出现过的组合。为什么不再用「整套
参数完全相同才算一票」：参数是连续/多取值的，同一片高分区的票会被切碎（实测 20 折里 9 折都选了
「回看20 + 周期5」，却因权重上限 0.5/0.6 之分只记成 5 票）。
**绝不按样本外表现挑参数**——否则等于在样本外上二次寻优，样本外成绩立刻失真。这样得到的「晋级参数」
写回 bt_params 成为 is_active=TRUE 的新策略，旧的失效策略删除；随后回测框架在整段区间上跑出
明细页看到的那条净值，那是**上线口径（全样本拟合）**，不是样本外成绩。

综合分 = 年化收益 / 卡玛比率 / 夏普 三项**组内排名百分位**的等权平均（0~1，越大越好）。
为什么先排名再平均：三项量纲差得多（实测 年化≈0.21、卡玛≈1.1、夏普≈1.0），
直接平均会让年化的实际权重只剩约 9%，谈不上"等权"。组内排名百分位把三项各自
映射到 0~1 再等权平均，既是真正的等权，也天然抗异常值（卡玛在回撤≈0 时会爆掉）。
打分口径全流程只有一套：训练窗用它选参数；样本外只做验证，不参与选择。

一次寻优产出一个批次：opt_walk 一行（整体结论）+ opt_folds N 行（每折明细）+
opt_oos_nav 一行一天（各折样本外段拼成的那条净值曲线，页面画折分界用）+
opt_results 每折各一批（该折训练窗的全部候选，每折一个独立 opt_id，互不覆盖）。

样本太短（不足 train_days + test_days）时退回**单段全样本寻优**（旧的单批行为），
只写 opt_results，不产生 opt_walk/opt_folds 行。

候选参数用多进程并行计算（缺省 = CPU 核数，设 1 则串行）。

运行：python -m framework.optimize_framework [workers] [train_days] [test_days] [step_days]
    python -m framework.optimize_framework                  # 缺省：并行核数=CPU，训练756/样本外126/步长126 交易日
    python -m framework.optimize_framework 8                # 指定 8 个并行进程
    python -m framework.optimize_framework 8 504 126 126    # 训练 2 年、样本外半年、步长半年
    python -m framework.optimize_framework 8 756 126 21     # 步长缩到 1 个月：折更多、参数切换更密
                                                            # （重叠的样本外段会被裁掉，曲线仍逐日唯一）
                                                             # 注意折数与耗时成正比：每折都要重跑整片网格
重算：python -m framework.optimize_framework recompute [--push] [opt_id ...]
    # 换了投票规则又想套用到已落库的批次时用它：只读 opt_results 里现成的候选与综合分，
    # 重算 opt_walk.winner_params / winner_folds 与 opt_folds.selected，**不重跑寻优与样本外**；
    # 不带 opt_id 就是全部批次。带 --push 时再把同组旧策略换掉（旧行删除、bt_* 级联删除），
    # 因此随后要重跑回测刷新）。
"""

import itertools
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

from psycopg.types.json import Jsonb

from .backtest_framework import BacktestFramework, sync_strategy_name


# ------------------------------------------------------------ 多进程并行工作函数
def _calc_one(strategy_method, cand, codes, start, end, data):
    """单个参数组合跑一遍回测引擎（内存 data，无 DB），返回 (params, metrics, oos_nav)。"""
    bt = BacktestFramework()
    rec = bt.engine(strategy_method, cand, codes, start, end, None, data=data)
    m = bt.compute_metrics([x[1] for x in rec["nav"]])
    return (cand, m, rec["nav"])


_WORKER_STATE = {}

def _init_worker(data, strategy_method, codes, start, end):
    """每个子进程初始化一次：把预载的价格矩阵等读进进程局部状态。"""
    _WORKER_STATE.update(data=data, method=strategy_method, codes=codes, start=start, end=end)

def _eval_candidate(cand):
    s = _WORKER_STATE
    return _calc_one(s["method"], cand, s["codes"], s["start"], s["end"], s["data"])


class OptimizeFramework:
    WORKERS = os.cpu_count() or 1   # 多进程并行数；设 1（或 0）则串行
    # 综合分的三个等权分量（先去量纲再平均，见 combined_scores）
    SCORE_PARTS = ["annual", "calmar", "sharpe"]

    # 滚动窗口缺省配置（交易日）：训练 3 年 → 样本外半年 → 步长半年
    # 步长可以小于样本外窗长（重新选参数更频繁，折更多）：重叠的样本外段由 splice_oos 裁掉，
    # 所以曲线仍逐日唯一、不重复计同一段行情
    TRAIN_DAYS = 756
    TEST_DAYS = 126
    STEP_DAYS = 126

    # 稳定性投票里「支撑折数」的口径：某参数取值进过前 TOP_PCT 的折数（见 stability_vote）。
    # 只用来报告支撑度，不参与挑选 —— 挑选用平均名次，避免依赖这个阈值。
    TOP_PCT = 0.10

    # 参数网格（要改搜索范围就改这里）
    # 注意：min_w 受成分数量约束 min_w * n_codes <= 1，不可行组合会在 candidates() 里自动过滤
    GRIDS = {
        "max_sharpe": {
            "lookback": [20, 40, 60, 90, 120, 180, 250],    # 约1月~1年
            "period": [5, 10, 20, 40, 60],                  # 周/双周/月/双月/季
            "min_w": [0.0, 0.05, 0.10],                     # 不设限/≥5%/≥10%
            "max_w": [0.3, 0.4, 0.5, 0.6],             # 上限 30%~满仓
        },
        "min_variance": {
            "lookback": [20, 40, 60, 90, 120, 180, 250],
            "period": [5, 10, 20, 40, 60],
            "min_w": [0.05, 0.08, 0.10],
            "max_w": [0.3, 0.4, 0.5, 0.6],
        },
        "trend": {
            "long_window": [60, 120, 180, 250],
            "short_window": [5, 10, 20, 40, 60],
            "period": [1, 5, 10, 20],
        },
    }
    # 只落库寻优真正用的 7 项：期末净值 + 年化/最大回撤/夏普/年化波动/卡玛/索提诺。
    # compute_metrics 仍会算出全套 15 项（回测/模拟那边要），这里只挑这 7 项写 opt_* 表：
    # 窗口收益 ret_1d…ret_3y 在寻优里窗口太短大多为空、又从不参与打分，存了只会被误读。
    OPT_METRIC_COLS = [
        "nav_end", "ret_annual",
        "max_drawdown", "sharpe", "annual_vol", "calmar", "sortino",
    ]

    def __init__(self, workers=None, train_days=None, test_days=None, step_days=None):
        self.bt = BacktestFramework()
        self.workers = int(workers) if workers else self.WORKERS
        self.train_days = int(train_days) if train_days else self.TRAIN_DAYS
        self.test_days = int(test_days) if test_days else self.TEST_DAYS
        # 步长 < 样本外窗长时相邻两折的样本外窗会重叠，拼接时只取「上一折结束之后」的部分
        self.step_days = int(step_days) if step_days else self.STEP_DAYS

    def connect(self):
        return self.bt.connect()

    # ------------------------------------------------------------ 候选与分数
    def candidates(self, strategy_method, n_codes):
        grid = self.GRIDS[strategy_method]
        if strategy_method == "trend":
            for lw, sw, p in itertools.product(grid["long_window"], grid["short_window"], grid["period"]):
                if sw < lw:
                    yield {"long_window": lw, "short_window": sw, "period": p}
        else:
            keys = ["lookback", "period", "min_w", "max_w"]
            for vals in itertools.product(*(grid[k] for k in keys)):
                d = dict(zip(keys, vals))
                # 可行性：每只至少 min_w，n_codes 只加起来不能超过 1
                if d["min_w"] <= d["max_w"] and d["min_w"] * n_codes <= 1.0 + 1e-12:
                    yield d

    # ------------------------------------------------------------ 综合分
    @staticmethod
    def _metric(m, key):
        """取指标；缺失或非有限值按最差处理（排名垫底）。"""
        try:
            v = float(m.get(key))
        except (TypeError, ValueError):
            return float("-inf")
        return v if math.isfinite(v) else float("-inf")

    @staticmethod
    def _pct_rank(vals):
        """排名百分位（0~1，越大越好）：最好=1.0、最差=0.0；并列取平均名次。"""
        n = len(vals)
        if n <= 1:
            return [1.0] * n
        order = sorted(range(n), key=lambda i: vals[i])
        pct = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0          # 并列区间 [i, j] 取平均名次
            for k in range(i, j + 1):
                pct[order[k]] = avg_rank / (n - 1)
            i = j + 1
        return pct

    def combined_scores(self, rows):
        """rows: [(params, metrics, nav), ...] → [(params, metrics, nav, score), ...]

        score = 年化 / 卡玛 / 夏普 三项组内排名百分位的等权平均，区间 0~1，越大越好。
        先排名是为了去掉量纲、让三项真正等权（见模块 docstring）。
        """
        cols = {
            "annual": [self._metric(m, "ret_annual") for _, m, _ in rows],
            # 卡玛直接取 compute_metrics 算好的值（口径见 backtest_framework.compute_metrics），
            # 不再在这里重算一遍，避免同一指标两套算法
            "calmar": [self._metric(m, "calmar") for _, m, _ in rows],
            "sharpe": [self._metric(m, "sharpe") for _, m, _ in rows],
        }
        pcts = {k: self._pct_rank(v) for k, v in cols.items()}
        w = 1.0 / len(self.SCORE_PARTS)
        return [(*r, sum(pcts[k][i] for k in self.SCORE_PARTS) * w)
                for i, r in enumerate(rows)]

    @staticmethod
    def best_of(scored):
        """scored 里综合分最高的一行（并列取靠前的候选，结果稳定可复现）。"""
        return max(range(len(scored)), key=lambda i: (scored[i][-1], -i))

    # ------------------------------------------------------------ 取数与搜索
    def next_opt_id(self, conn):
        """下一个批号。

        `opt_results`（候选批次）、`opt_walk`（滚动批次）、`opt_folds.is_opt_id`（各折候选批次）
        共用同一套编号，所以必须三张表一起取最大 —— 只看 opt_results 的话，滚动批次的批号
        会比它大（滚动批次不写 opt_results），下次分配就会撞 opt_walk 的主键。
        """
        with conn.cursor() as cur:
            cur.execute("SELECT GREATEST("
                        "(SELECT COALESCE(max(opt_id), 0) FROM opt_results), "
                        "(SELECT COALESCE(max(opt_id), 0) FROM opt_walk), "
                        "(SELECT COALESCE(max(is_opt_id), 0) FROM opt_folds)) + 1")
            return cur.fetchone()[0]

    def load_data(self, conn, codes, start, end, strategy_method):
        grid = self.GRIDS[strategy_method]
        max_win = max(grid.get("lookback", [0]) + grid.get("long_window", [0]))
        max_period = max(grid["period"])
        return self.bt.load_prices(conn, codes, start, end, max_win + max_period + 5)

    def trade_days(self, conn, start, end):
        """整段区间内的交易日（与回测框架同一个日历来源）。"""
        with conn.cursor() as cur:
            cur.execute("SELECT cal_date FROM trade_calendar WHERE cal_date BETWEEN %s AND %s "
                        "AND is_open ORDER BY cal_date", (start, end))
            return [r[0] for r in cur.fetchall()]

    def walk_windows(self, days, train, test, step):
        """把交易日序列切成 [(训练起, 训练止, 样本外起, 样本外止), ...]。

        第 i 折：训练窗 days[i : i+train] → 样本外窗紧接其后 days[i+train : i+train+test]，
        下一折起点 i += step。训练窗与样本外窗之间不留空档（避免跳过的行情被浪费）。
        """
        out = []
        i = 0
        while i + train + test <= len(days):
            out.append((days[i], days[i + train - 1], days[i + train], days[i + train + test - 1]))
            i += step
        return out

    def run_grid(self, data, strategy_method, codes, start, end):
        """对一个窗口跑完整个参数网格 → [(params, metrics, nav), ...]（不含分数）。"""
        cands = list(self.candidates(strategy_method, len(codes)))
        if not cands:
            return []
        if self.workers > 1 and len(cands) > 1:
            try:
                workers = min(self.workers, len(cands))
                with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                                         initargs=(data, strategy_method, codes, start, end)) as ex:
                    rows = list(ex.map(_eval_candidate, cands,
                                       chunksize=max(1, len(cands) // (workers * 4))))
                print(f"  [{strategy_method}] {len(cands)}/{len(cands)} 组完成（并行 {workers} 进程）", flush=True)
                return rows
            except Exception as e:
                print(f"  [{strategy_method}] 并行失败({e})，回退串行", flush=True)
        rows = []
        for i, cand in enumerate(cands, 1):
            rows.append(_calc_one(strategy_method, cand, codes, start, end, data))
            if i == 1 or i % 50 == 0 or i == len(cands):
                print(f"  [{strategy_method}] {i}/{len(cands)} 组完成", flush=True)
        return rows

    def run_one(self, conn, strategy_method, codes, start, end):
        """单段全样本：只跑一次网格，返回 [(params, metrics, nav), ...]。"""
        data = self.load_data(conn, codes, start, end, strategy_method)
        return self.run_grid(data, strategy_method, codes, start, end)

    # ------------------------------------------------------------ 落库
    def write_results(self, conn, opt_id, strategy_method, codes, start, end, scored):
        """写一批候选到 opt_results（含综合分与本批 selected），返回胜出行。"""
        best_i = self.best_of(scored)
        with conn.cursor() as cur:
            for i, (params, m, _nav, score) in enumerate(scored, start=1):
                cur.execute(
                    "INSERT INTO opt_results (opt_id, candidate, strategy_method, "
                    "codes, start_date, end_date, params, " + ", ".join(self.OPT_METRIC_COLS) +
                    ", score, selected) VALUES (%s,%s,%s,%s,%s,%s,%s,"
                    + ",".join(["%s"] * len(self.OPT_METRIC_COLS)) + ",%s,%s)",
                    [opt_id, i, strategy_method, list(codes), start, end,
                     Jsonb(params)] + [m.get(c) for c in self.OPT_METRIC_COLS]
                    + [score, i - 1 == best_i])
        conn.commit()
        return scored[best_i]

    def _fold_top(self, cands):
        """一折里参与计票的候选：按综合分降序取前 TOP_PCT（至少 1 个）。"""
        return cands[:max(1, int(round(len(cands) * self.TOP_PCT)))]

    def stability_vote(self, fold_cands):
        """各折候选批（[(params, score), ...]，按综合分降序）→ 参数级稳定性投票。

        规则：每折给每个参数取值记一个名次 —— 该取值在这一折里最靠前的那套候选排第几；
        跨折取**平均名次**，各参数取平均名次最好的取值拼成稳定组合；再在所有真实候选里投影到
        逐项匹配最多的那一套 —— 保证晋级参数一定是历史上真实出现过的完整组合。

        为什么不用「整套参数完全相同才算一票」：参数是连续/多取值的，同一片高分区的票会被
        切碎 —— 实测 20 折里 9 折都选「回看20 + 周期5」，却因权重上限 0.5/0.6 之分只记成 5 票，
        票数还被当成可信度指标用。
        为什么用名次而不是「进入前 K 的折数」：K 是个必须挑的阈值，而它很不稳 —— 420 套取前 42
        （TOP_PCT=10%）时几乎每个取值都能进 20/20 折（判据饱和，谁赢全看并列规则），K 再小就
        退化成只看第一名。平均名次不需要阈值，且天然惩罚「只在个别折里偶然靠前」的取值。

        依旧刻意**不看样本外**：一旦按样本外挑参数，样本外成绩就失去意义。
        返回 (params, support, diag)：support = 投影后那套参数里、最弱参数「进过前 TOP_PCT 的
        折数」（下界）；diag = {参数: [(取值, 入选折数, 入选次数, 平均名次), ...]}，按平均名次升序。
        """
        if not fold_cands:
            return None, 0, {}
        keys = sorted(fold_cands[0][0][0].keys())
        n_folds = len(fold_cands)
        rank_sum = {k: {} for k in keys}       # 参数 -> 取值 -> 各折最好名次之和
        in_top = {k: {} for k in keys}         # 参数 -> 取值 -> [进过前 K 的折数, 入选次数]
        for cands in fold_cands:
            top_n = len(self._fold_top(cands))
            best = {k: {} for k in keys}
            for rank, (params, _score) in enumerate(cands, start=1):
                for k in keys:
                    best[k].setdefault(params[k], rank)     # 该取值在这一折的最好名次
                    if rank <= top_n:
                        cnt = in_top[k].setdefault(params[k], [0, 0])
                        cnt[1] += 1
            for k in keys:
                for v, rank in best[k].items():
                    rank_sum[k][v] = rank_sum[k].get(v, 0) + rank
                    if rank <= top_n:
                        in_top[k][v][0] += 1
        stable, diag = {}, {}
        for k in keys:
            # 某个取值可能在所有折里都没进过前 K，in_top 里就没有它 —— 按 0 折算。
            # 排序是稳定的：平均名次与支撑折数都并列时，保留首次出现顺序 → 结果可复现。
            items = [(v, r, in_top[k].get(v, (0, 0))) for v, r in rank_sum[k].items()]
            items.sort(key=lambda x: (x[1], -x[2][0]))
            stable[k] = items[0][0]
            diag[k] = [(v, e[0], e[1], r / n_folds) for v, r, e in items]
        winner, hit = None, None
        for cands in fold_cands:
            for params, score in cands:
                n = sum(1 for k in keys if params[k] == stable[k])
                if hit is None or (n, score) > hit:
                    hit, winner = (n, score), params
        return winner, min(in_top[k].get(winner[k], (0, 0))[0] for k in keys), diag

    def log_vote(self, diag, winner, support, folds):
        """把投票过程打到日志：每个参数的三个最稳取值（平均名次越小越稳）。"""
        print(f"[OPT] 参数级稳定性投票（名次口径；支撑 = 进过前 {self.TOP_PCT:.0%} 的折数）：", flush=True)
        for k, items in diag.items():
            print(f"     {k:<13}" + "  ".join(
                f"{v} 均名次{ms:.1f}({nf}折/{cnt}次)" for v, nf, cnt, ms in items[:3]), flush=True)
        print(f"     → 晋级参数={winner}（最弱参数支撑 {support}/{folds} 折）", flush=True)

    def begin_walk(self, conn, opt_id, method, codes, start, end):
        """开头就写 opt_walk 占位行（folds=0）。

        两个作用：① 折明细要能立刻引用它；② 折候选批次一落库就靠 opt_folds.is_opt_id 被
        胜出参数表排除掉，否则运行中的「每折那批」会被误当成单段寻优批次显示出来。
        顺手清掉同组上次跑崩留下的占位行（连带它的折明细与候选）。
        """
        with conn.cursor() as cur:
            cur.execute(
                "SELECT opt_id FROM opt_walk WHERE strategy_method = %s AND codes = %s "
                "AND start_date IS NOT DISTINCT FROM %s AND end_date IS NOT DISTINCT FROM %s "
                "AND folds = 0", (method, list(codes), start, end))
            for (old_id,) in cur.fetchall():
                cur.execute("DELETE FROM opt_results WHERE opt_id IN "
                            "(SELECT is_opt_id FROM opt_folds WHERE opt_id = %s)", (old_id,))
                cur.execute("DELETE FROM opt_folds WHERE opt_id = %s", (old_id,))
                cur.execute("DELETE FROM opt_walk WHERE opt_id = %s", (old_id,))
            cur.execute(
                "INSERT INTO opt_walk (opt_id, strategy_method, codes, start_date, end_date, "
                "folds, train_days, test_days, step_days, winner_params, winner_folds) "
                "VALUES (%s,%s,%s,%s,%s,0,%s,%s,%s,%s,0)",
                (opt_id, method, list(codes), start, end,
                 self.train_days, self.test_days, self.step_days, Jsonb({})))
        conn.commit()

    def write_fold(self, conn, opt_id, fold_no, is_opt_id, window, params, m_train, score, m_test):
        """写一折明细。selected 先占 FALSE，全部折跑完后由 finish_walk 按晋级参数回标。"""
        ts, te, ss, se = window
        train_cols = [f"train_{c}" for c in self.OPT_METRIC_COLS] + ["train_score"]
        oos_cols = [f"oos_{c}" for c in self.OPT_METRIC_COLS]
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO opt_folds (opt_id, fold, is_opt_id, train_start, train_end, "
                "test_start, test_end, params, " + ", ".join(train_cols) + ", "
                + ", ".join(oos_cols) + ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,"
                + ",".join(["%s"] * len(train_cols)) + ","
                + ",".join(["%s"] * len(oos_cols)) + ")",
                [opt_id, fold_no, is_opt_id, ts, te, ss, se, Jsonb(params)]
                + [m_train.get(c) for c in self.OPT_METRIC_COLS] + [score]
                + [m_test.get(c) for c in self.OPT_METRIC_COLS])
        conn.commit()

    def drop_walk(self, conn, opt_id):
        """撤掉占位行（一折都没跑成时用）。同组下次寻优的 begin_walk 也会自愈清理。"""
        with conn.cursor() as cur:
            cur.execute("DELETE FROM opt_walk WHERE opt_id = %s", (opt_id,))
        conn.commit()

    def write_winner(self, conn, opt_id, winner, winner_folds):
        """写晋级参数与支撑折数，并把「该折胜出参数恰好是它」的那些折标出来。"""
        with conn.cursor() as cur:
            cur.execute("UPDATE opt_walk SET winner_params = %s, winner_folds = %s WHERE opt_id = %s",
                        (Jsonb(winner), winner_folds, opt_id))
            # JSONB 相等是按结构比，键顺序无关；晋级参数本来就是整份 dict，能直接对上
            cur.execute("UPDATE opt_folds SET selected = (params = %s) WHERE opt_id = %s",
                        (Jsonb(winner), opt_id))
        conn.commit()

    def finish_walk(self, conn, opt_id, folds, winner, winner_folds, oos_metrics):
        """跑完收尾：补 opt_walk 的折数与样本外拼接指标，再写晋级参数并回标各折 selected。"""
        oos_cols = [f"oos_{c}" for c in self.OPT_METRIC_COLS]
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE opt_walk SET folds = %s, " + ", ".join(f"{c} = %s" for c in oos_cols)
                + " WHERE opt_id = %s",
                [len(folds)] + [oos_metrics.get(c) for c in self.OPT_METRIC_COLS] + [opt_id])
        conn.commit()
        self.write_winner(conn, opt_id, winner, winner_folds)

    def push_best(self, conn, strategy_method, codes, start, end, opt_id, params, old_ids, sname=None):
        p = params
        vals = [sname or f"{strategy_method}_{start}_{end}", strategy_method,
                p.get("period"), p.get("lookback"), p.get("min_w"), p.get("max_w"),
                p.get("long_window"), p.get("short_window"), list(codes), start, end,
                "optimize", opt_id, True]
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bt_params (strategy_name, strategy_method, period, lookback, "
                "min_w, max_w, long_window, short_window, codes, start_date, end_date, "
                "source, opt_id, is_active) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (strategy_method, period, lookback, min_w, max_w, long_window, "
                "short_window, codes, start_date, end_date) "
                "DO UPDATE SET is_active = TRUE, source = 'optimize', opt_id = EXCLUDED.opt_id, "
                "strategy_name = EXCLUDED.strategy_name "
                "RETURNING strategy_id", vals)
            winner_id = cur.fetchone()[0]
            cur.execute("DELETE FROM bt_params WHERE strategy_id = ANY(%s) AND is_active = FALSE",
                        (old_ids,))
        conn.commit()
        # 晋级可能就地改了 strategy_name（ON CONFLICT DO UPDATE），结果表副本要同步
        sync_strategy_name(conn, winner_id, vals[0])
        return winner_id

    # ------------------------------------------------------------ 单折与整段
    def run_fold(self, conn, opt_id, fold_no, method, codes, window):
        """一折：训练窗跑全网格选参数 → 样本外窗只跑胜出参数 → 立刻把这一折落库。

        返回 (is_opt_id, (params, train_metrics, train_score, oos_metrics, oos_nav), cands)；
        cands 是这一折全部候选的 (params, score)（按综合分降序），给稳定性投票用 —— 只留参数与
        综合分，净值写完库就丢，不占内存。
        """
        ts, te, ss, se = window
        data = self.load_data(conn, codes, ts, te, method)
        scored = self.combined_scores(self.run_grid(data, method, codes, ts, te))
        if not scored:
            return None
        is_opt_id = self.next_opt_id(conn)
        params, m_train, _nav, score = self.write_results(conn, is_opt_id, method, codes, ts, te, scored)
        cands = sorted(((prm, sc) for prm, _m, _n, sc in scored), key=lambda x: -x[1])
        # 样本外：只跑这一组参数
        tdata = self.load_data(conn, codes, ss, se, method)
        _, m_test, oos_nav = _calc_one(method, params, codes, ss, se, tdata)
        self.write_fold(conn, opt_id, fold_no, is_opt_id, window, params, m_train, score, m_test)
        return (is_opt_id, (params, m_train, score, m_test, oos_nav), cands)

    def walk_forward(self, conn, opt_id, method, codes, days):
        """滚动窗口 + 样本外验证 → (folds, winner, winner_folds, oos_metrics)，折不够返回 None。"""
        windows = self.walk_windows(days, self.train_days, self.test_days, self.step_days)
        if not windows:
            return None
        folds, fold_cands = [], []
        for k, w in enumerate(windows, start=1):
            print(f"[OPT] 第 {k}/{len(windows)} 折 训练 {w[0]}~{w[1]} 样本外 {w[2]}~{w[3]}", flush=True)
            got = self.run_fold(conn, opt_id, k, method, codes, w)
            if got is None:
                print(f"[OPT] 第 {k} 折无候选可跑，跳过", flush=True)
                continue
            is_opt_id, row, cands = got
            folds.append((k, w, is_opt_id, row))
            fold_cands.append(cands)
            params, m_train, score, m_test, _nav = row
            print(f"     胜出 params={params} 训练综合分={score:.4f} "
                  f"训练年化={self._show(m_train.get('ret_annual'))} "
                  f"样本外年化={self._show(m_test.get('ret_annual'))}", flush=True)
        if not folds:
            return None
        winner, winner_folds, diag = self.stability_vote(fold_cands)
        self.log_vote(diag, winner, winner_folds, len(folds))
        oos_nav = self.splice_oos([(k, row[0], row[4]) for k, _w, _oid, row in folds])
        self.write_oos_nav(conn, opt_id, oos_nav)
        oos_metrics = self.bt.compute_metrics([v for _, v, _ in oos_nav])
        return folds, winner, winner_folds, oos_metrics

    def splice_oos(self, folds):
        """各折样本外段 → 一条拼接净值 [(交易日, 净值, 折号), ...]。

        每折都是独立回测、起点 1.0，所以先按「本段保留的第一天」归一再乘到累计水平上。
        步长 < 样本外窗长时后一折会与前一折重叠：重叠的交易日直接丢掉（只取上一折结束之后
        的部分），并用本段实际保留的第一天重新归一 —— 这样曲线逐日唯一、不重复计同一段行情。
        步长 ≥ 窗长（缺省 126/126）时每段都整段保留，结果与逐段首尾相接完全一致。
        """
        out, level, covered = [], 1.0, None
        for fold_no, _params, nav in folds:
            seg = [(row[0], row[1]) for row in nav if covered is None or row[0] > covered]
            if not seg:
                continue
            base = seg[0][1]
            for d, v in seg:
                out.append((d, level * v / base, fold_no))
            level = out[-1][1]
            covered = out[-1][0]
        return out

    def write_oos_nav(self, conn, opt_id, oos_nav):
        """写样本外拼接曲线（一行一个交易日，带折号）。"""
        with conn.cursor() as cur:
            cur.execute("DELETE FROM opt_oos_nav WHERE opt_id = %s", (opt_id,))
            cur.executemany(
                "INSERT INTO opt_oos_nav (opt_id, trade_date, nav, fold) VALUES (%s,%s,%s,%s)",
                [(opt_id, d, v, k) for d, v, k in oos_nav])
        conn.commit()

    def rebuild_oos_nav(self, conn, opt_id):
        """按 opt_folds 重算并覆盖某次寻优的样本外拼接曲线（与 oos_* 指标同源）。

        只重跑每折样本外窗那一小段回测（8 折 × 126 天，几秒钟），不碰训练窗与选参数结果；
        曲线丢了、或想换拼接规则时用它重建 —— 这也是把曲线单独存一张表的意义。
        """
        with conn.cursor() as cur:
            cur.execute("SELECT strategy_method, codes, train_days, test_days, step_days "
                        "FROM opt_walk WHERE opt_id = %s", (opt_id,))
            head = cur.fetchone()
            if head is None:
                return None
            method, codes, train_days, test_days, step_days = head
            cur.execute("SELECT fold, test_start, test_end, params FROM opt_folds "
                        "WHERE opt_id = %s ORDER BY fold", (opt_id,))
            rows = cur.fetchall()
        if not rows:
            return None
        folds = []
        for fold_no, ss, se, params in rows:
            data = self.load_data(conn, list(codes), ss, se, method)
            _, _m, nav = _calc_one(method, params, list(codes), ss, se, data)
            folds.append((fold_no, params, nav))
        oos_nav = self.splice_oos(folds)
        self.write_oos_nav(conn, opt_id, oos_nav)
        return oos_nav

    @staticmethod
    def _show(v):
        """收益类指标按百分比显示。"""
        try:
            return f"{float(v) * 100:.2f}%"
        except (TypeError, ValueError):
            return "—"

    @staticmethod
    def _num(v):
        """比率类指标（夏普/卡玛/索提诺）按普通数字显示，不能乘 100。"""
        try:
            return f"{float(v):.2f}"
        except (TypeError, ValueError):
            return "—"

    # ------------------------------------------------------------ 主流程
    def run(self):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT strategy_id, strategy_name, strategy_method, codes, start_date, end_date "
                            "FROM bt_params WHERE is_active = FALSE ORDER BY strategy_id")
                inactive = cur.fetchall()
            if not inactive:
                print("[OPT] 无失效策略，不执行")
                return
            groups = {}
            for sid, sname, method, codes, start, end in inactive:
                groups.setdefault((method, tuple(sorted(codes)), start, end), []).append((sid, sname))
            latest = None
            for (method, codes, start, end), items in groups.items():
                ids = [x[0] for x in items]
                # end_date 留空 = 跟最新行情：按本次运行的最新交易日评估，
                # 但晋级时把 NULL 原样写回（否则候选晋级后会被冻结成具体日期，不再跟最新）
                end_run = end
                if end_run is None:
                    if latest is None:
                        latest = self.bt._latest_trade_date(conn)
                    end_run = latest
                base_name = next((x[1] for x in items if x[1]), None) or f"{method}_{start}_{end_run}"
                print(f"[OPT] method={method} 区间={start}~{end_run}"
                      f"{'（跟最新）' if end is None else ''} name={base_name} "
                      f"窗口={self.train_days}/{self.test_days}/{self.step_days} 交易日", flush=True)
                days = self.trade_days(conn, start, end_run)
                windows = self.walk_windows(days, self.train_days, self.test_days, self.step_days)
                if not windows:                     # 样本太短：退回单段全样本寻优（只写 opt_results）
                    print(f"[OPT] 交易日仅 {len(days)} 天，不足训练{self.train_days}+样本外{self.test_days}，"
                          f"退回单段全样本寻优", flush=True)
                    rows = self.run_one(conn, method, list(codes), start, end_run)
                    if not rows:
                        print(f"[OPT] {method} 无候选可跑")
                        continue
                    scored = self.combined_scores(rows)
                    opt_id = self.next_opt_id(conn)
                    best = self.write_results(conn, opt_id, method, list(codes), start, end_run, scored)
                    winner_id = self.push_best(conn, method, list(codes), start, end,
                                               opt_id, best[0], ids, base_name)
                    print(f"[OPT] 单段胜出 strategy_id={winner_id} params={best[0]} "
                          f"年化={self._show(best[1].get('ret_annual'))}", flush=True)
                    continue
                # 先占批号、写 opt_walk 占位行，再逐折跑（每折跑完立刻连同折明细落库）
                opt_id = self.next_opt_id(conn)
                self.begin_walk(conn, opt_id, method, list(codes), start, end)
                walk = self.walk_forward(conn, opt_id, method, list(codes), days)
                if walk is None:                    # 每折都没跑成：撤掉占位行
                    self.drop_walk(conn, opt_id)
                    continue
                folds, winner, winner_folds, oos_metrics = walk
                self.finish_walk(conn, opt_id, folds, winner, winner_folds, oos_metrics)
                winner_id = self.push_best(conn, method, list(codes), start, end,
                                           opt_id, winner, ids, base_name)
                print(f"[OPT] 滚动寻优 opt_id={opt_id} {len(folds)} 折 → "
                      f"晋级 strategy_id={winner_id}", flush=True)
                print(f"[OPT] 样本外拼接：年化={self._show(oos_metrics.get('ret_annual'))} "
                      f"回撤={self._show(oos_metrics.get('max_drawdown'))} "
                      f"夏普={self._num(oos_metrics.get('sharpe'))} "
                      f"卡玛={self._num(oos_metrics.get('calmar'))}", flush=True)
        finally:
            conn.close()


    @staticmethod
    def _same_num(a, b):
        """数值列比较：库里的 NUMERIC 是 Decimal、参数是 float/int/None，统一按 float 比。"""
        if a is None or b is None:
            return a is None and b is None
        return float(a) == float(b)

    def promote(self, conn, method, codes, start, end, opt_id, params):
        """把晋级参数落成 bt_params 的有效行：同组里参数不同的旧行先删，再 upsert 冠军。

        与 push_best 的差别只在「旧行从哪来」——寻优时是刚被置为失效的那几条，重算时是库里
        当前同组（同 method/codes/区间）的行。不先删就会新旧两条并存为有效策略。
        该组当前没有任何行（历史批次、策略早被后续批次替换掉了）时**不造新策略**，返回 (None, [])。
        删 bt_params 会级联删掉它的 bt_* 结果，所以之后要重跑回测刷新。
        """
        cols = ("period", "lookback", "min_w", "max_w", "long_window", "short_window")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT strategy_id, strategy_name, " + ", ".join(cols) + " FROM bt_params "
                "WHERE strategy_method = %s AND codes = %s "
                "AND start_date IS NOT DISTINCT FROM %s AND end_date IS NOT DISTINCT FROM %s "
                "ORDER BY strategy_id", (method, list(codes), start, end))
            rows = cur.fetchall()
            if not rows:
                return None, []
            keep = {r[0] for r in rows
                    if all(self._same_num(r[2 + i], params.get(c)) for i, c in enumerate(cols))}
            drop = [r[0] for r in rows if r[0] not in keep]
            if drop:
                cur.execute("DELETE FROM bt_params WHERE strategy_id = ANY(%s)", (drop,))
            sname = next((r[1] for r in rows if r[1]), None)
        conn.commit()
        sid = self.push_best(conn, method, list(codes), start, end, opt_id, params, [], sname)
        return sid, drop

    def recompute(self, opt_ids=None, push=False):
        """按当前投票规则重算已落库批次的晋级参数（不重跑寻优、不重算样本外与拼接曲线）。

        读 opt_results 里现成的候选与综合分 → 重新投票 → 更新 opt_walk.winner_params /
        winner_folds 与 opt_folds.selected；push=True 时再把同组旧策略换成新冠军。
        """
        conn = self.connect()
        try:
            sql = ("SELECT opt_id, strategy_method, codes, start_date, end_date, winner_params, "
                   "winner_folds FROM opt_walk WHERE folds > 0")
            args = []
            if opt_ids:
                sql += " AND opt_id = ANY(%s)"
                args.append(list(opt_ids))
            with conn.cursor() as cur:
                cur.execute(sql + " ORDER BY opt_id", args)
                walks = cur.fetchall()
            if not walks:
                print("[OPT] 没有可重算的滚动批次", flush=True)
                return
            for opt_id, method, codes, start, end, old_params, old_folds in walks:
                fold_cands = []
                with conn.cursor() as cur:
                    cur.execute("SELECT is_opt_id FROM opt_folds WHERE opt_id = %s ORDER BY fold",
                                (opt_id,))
                    for (batch,) in cur.fetchall():
                        cur.execute("SELECT params, score FROM opt_results WHERE opt_id = %s "
                                    "ORDER BY score DESC, candidate", (batch,))
                        fold_cands.append([(p, float(s)) for p, s in cur.fetchall()])
                if not fold_cands:
                    print(f"[OPT] opt_id={opt_id} 无候选明细，跳过", flush=True)
                    continue
                winner, support, _diag = self.stability_vote(fold_cands)
                self.write_winner(conn, opt_id, winner, support)
                old = json.dumps(old_params or {}, sort_keys=True, ensure_ascii=False)
                new = json.dumps(winner, sort_keys=True, ensure_ascii=False)
                print(f"[OPT] opt_id={opt_id} {method} {len(fold_cands)} 折 支撑 {support}："
                      f"{'参数未变' if old == new else '参数变化'}\n"
                      f"     旧 {old}（{old_folds}）\n     新 {new}", flush=True)
                if push:
                    sid, drop = self.promote(conn, method, list(codes), start, end, opt_id, winner)
                    if sid is None:
                        print("     → 跳过晋级：该组当前没有生效策略（历史批次，不重造）", flush=True)
                    else:
                        print(f"     → strategy_id={sid}"
                              f"{'，删除旧行 ' + str(drop) if drop else '（参数未变，沿用原行）'}",
                              flush=True)
        finally:
            conn.close()


def main(argv):
    """命令行入口：缺省跑寻优；`recompute [--push] [opt_id ...]` 按当前规则重算已落库批次。"""
    if argv and argv[0] == "recompute":
        ids = [int(a) for a in argv[1:] if not a.startswith("-")]
        OptimizeFramework().recompute(ids or None, push="--push" in argv)
        return
    OptimizeFramework(*argv[0:4]).run()


if __name__ == "__main__":
    main(sys.argv[1:])
