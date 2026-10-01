-- =========================================================================
-- ETF 策略框架 —— 一键建库脚本（18 张表）
-- 用法：psql -f schema.sql  （或由项目内脚本按 ';' 拆分执行）
-- 说明：幂等可重复执行（只 CREATE IF NOT EXISTS + 注释，不动已有数据）。
--       旧版（run_id 型）bt_* 已在首次迁移时清理完毕；若确需「彻底重建
--       回测/模拟结果表」，再手动执行下面这行（会清空现有策略与结果，慎用！）：
--   DROP TABLE IF EXISTS bt_calendar, bt_trades, bt_nav, bt_holdings, bt_benchmark_nav CASCADE;
--   DROP TABLE IF EXISTS sim_benchmark_nav, sim_holdings, sim_trades, sim_calendar, sim_nav, sim_params, sim_account CASCADE;
-- =========================================================================

-- =========================================================================
-- 〇、行情三表（etf_sync.py 增量写入；三框架只读）
-- =========================================================================
CREATE TABLE IF NOT EXISTS trade_calendar (
    cal_date   DATE NOT NULL,
    is_open    BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (cal_date)
);
CREATE TABLE IF NOT EXISTS etf_bars_raw (
    code       TEXT NOT NULL,
    name       TEXT NOT NULL,
    trade_date DATE NOT NULL,
    open       NUMERIC(10,3),
    high       NUMERIC(10,3),
    low        NUMERIC(10,3),
    close      NUMERIC(10,3),
    volume     BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (code, trade_date)
);
CREATE TABLE IF NOT EXISTS etf_bars_hfq (
    code       TEXT NOT NULL,
    name       TEXT NOT NULL,
    trade_date DATE NOT NULL,
    open       NUMERIC(10,3),
    high       NUMERIC(10,3),
    low        NUMERIC(10,3),
    close      NUMERIC(10,3),
    volume     BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (code, trade_date)
);

-- =========================================================================
-- 一、回测参数表（= 策略表）
-- =========================================================================
CREATE TABLE IF NOT EXISTS bt_params (
    strategy_id     SERIAL PRIMARY KEY,
    strategy_name   TEXT,
    strategy_method TEXT NOT NULL
                    CHECK (strategy_method IN ('max_sharpe', 'min_variance', 'trend')),
    period       INT,
    lookback     INT,
    min_w        NUMERIC(12,8),
    max_w        NUMERIC(12,8),
    long_window  INT,
    short_window INT,
    codes        TEXT[]      NOT NULL,
    start_date   DATE        NOT NULL,
    end_date     DATE,                       -- NULL = 自动跟到行情最新交易日
    source       TEXT        NOT NULL DEFAULT 'manual'
                 CHECK (source IN ('manual', 'optimize')),
    opt_id       INT,
    is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    nav_end       NUMERIC(16,6),
    ret_total     NUMERIC(12,6),
    ret_annual    NUMERIC(12,6),
    ret_1d        NUMERIC(12,6),
    ret_1w        NUMERIC(12,6),
    ret_1m        NUMERIC(12,6),
    ret_3m        NUMERIC(12,6),
    ret_1y        NUMERIC(12,6),
    ret_2y        NUMERIC(12,6),
    ret_3y        NUMERIC(12,6),
    max_drawdown  NUMERIC(12,6),
    sharpe        NUMERIC(12,6),
    annual_vol    NUMERIC(12,6),
    calmar        NUMERIC(12,6),
    sortino       NUMERIC(12,6),
    benchmark     TEXT NOT NULL DEFAULT 'sh510300',
    -- 基准指标：与上面策略指标**逐项一一对应**（同 15 个、同顺序，便于并排对齐比较）
    bm_nav_end    NUMERIC(16,6),
    bm_ret_total  NUMERIC(12,6),
    bm_ret_annual NUMERIC(12,6),
    bm_ret_1d     NUMERIC(12,6),
    bm_ret_1w     NUMERIC(12,6),
    bm_ret_1m     NUMERIC(12,6),
    bm_ret_3m     NUMERIC(12,6),
    bm_ret_1y     NUMERIC(12,6),
    bm_ret_2y     NUMERIC(12,6),
    bm_ret_3y     NUMERIC(12,6),
    bm_max_drawdown NUMERIC(12,6),
    bm_sharpe     NUMERIC(12,6),
    bm_annual_vol NUMERIC(12,6),
    bm_calmar     NUMERIC(12,6),
    bm_sortino    NUMERIC(12,6),
    excess_total  NUMERIC(12,6),
    excess_annual NUMERIC(12,6),
    metrics_at    TIMESTAMPTZ,
    CHECK (
        (strategy_method IN ('max_sharpe', 'min_variance')
            AND lookback IS NOT NULL AND period IS NOT NULL
            AND min_w IS NOT NULL AND max_w IS NOT NULL)
        OR
        (strategy_method = 'trend'
            AND long_window IS NOT NULL AND short_window IS NOT NULL AND period IS NOT NULL)
    ),
    CHECK (min_w IS NULL OR min_w >= 0.0),
    CHECK (max_w IS NULL OR max_w <= 1.0),
    CHECK (min_w IS NULL OR max_w IS NULL OR min_w <= max_w),
    CHECK (short_window IS NULL OR long_window IS NULL OR short_window < long_window),
    CHECK (period IS NULL OR period > 0),
    UNIQUE NULLS NOT DISTINCT (strategy_method, period, lookback, min_w, max_w,
                               long_window, short_window, codes, start_date, end_date)
);

-- =========================================================================
-- 二、回测结果表（5 张）
-- =========================================================================
CREATE TABLE IF NOT EXISTS bt_calendar (
    strategy_id   INT  NOT NULL REFERENCES bt_params(strategy_id) ON DELETE CASCADE,
    strategy_name TEXT,
    rebal_date    DATE NOT NULL,
    signal_date   DATE NOT NULL,
    PRIMARY KEY (strategy_id, rebal_date)
);
CREATE TABLE IF NOT EXISTS bt_trades (
    strategy_id   INT    NOT NULL REFERENCES bt_params(strategy_id) ON DELETE CASCADE,
    strategy_name TEXT,
    trade_date    DATE   NOT NULL,
    code          TEXT   NOT NULL,
    name          TEXT,
    side          TEXT   NOT NULL CHECK (side IN ('BUY','SELL')),
    qty           BIGINT NOT NULL,
    price         NUMERIC(14,4),
    amount        NUMERIC(18,2),
    fee           NUMERIC(14,2),
    PRIMARY KEY (strategy_id, trade_date, code, side)
);
CREATE TABLE IF NOT EXISTS bt_nav (
    strategy_id   INT  NOT NULL REFERENCES bt_params(strategy_id) ON DELETE CASCADE,
    strategy_name TEXT,
    trade_date    DATE NOT NULL,
    nav           NUMERIC(16,6),
    cash          NUMERIC(18,2),
    market_value  NUMERIC(18,2),
    total         NUMERIC(18,2),
    PRIMARY KEY (strategy_id, trade_date)
);
CREATE TABLE IF NOT EXISTS bt_holdings (
    strategy_id   INT  NOT NULL REFERENCES bt_params(strategy_id) ON DELETE CASCADE,
    strategy_name TEXT,
    rebal_date    DATE NOT NULL,
    code          TEXT NOT NULL,
    name          TEXT,
    weight        NUMERIC(12,8),
    signal_close  NUMERIC(14,4),
    signal_equity NUMERIC(18,2),
    target_qty    BIGINT,
    PRIMARY KEY (strategy_id, rebal_date, code)
);
CREATE TABLE IF NOT EXISTS bt_benchmark_nav (
    strategy_id INT  NOT NULL REFERENCES bt_params(strategy_id) ON DELETE CASCADE,
    benchmark   TEXT NOT NULL,
    trade_date  DATE NOT NULL,
    nav         NUMERIC(16,6),
    PRIMARY KEY (strategy_id, trade_date)
);

-- =========================================================================
-- 三、寻优结果表
-- 综合分 score = 年化收益 / 卡玛比率 / 夏普 三项「组内排名百分位」的等权平均（0~1）。
-- 一组候选（同 method + 同 codes + 同起止）产出 1 个 opt_id 批次、选中 1 个胜出参数。
-- =========================================================================
CREATE TABLE IF NOT EXISTS opt_results (
    opt_id          INT    NOT NULL,
    candidate       INT    NOT NULL,
    strategy_method TEXT   NOT NULL CHECK (strategy_method IN ('max_sharpe','min_variance','trend')),
    codes           TEXT[] NOT NULL,
    start_date      DATE   NOT NULL,
    end_date        DATE   NOT NULL,
    params          JSONB  NOT NULL,
    -- 只存寻优真正用的 7 项：期末净值 + 年化/最大回撤/夏普/年化波动/卡玛/索提诺
    -- （窗口收益 ret_1d…ret_3y 在寻优里窗口太短大多为空、又从不参与打分，所以不落库；
    --   回测/模拟表仍存全套 15 项 —— 那边是给人看的，口径不同）
    nav_end       NUMERIC(16,6),
    ret_annual    NUMERIC(12,6),
    max_drawdown  NUMERIC(12,6),
    sharpe        NUMERIC(12,6),
    annual_vol    NUMERIC(12,6),
    calmar        NUMERIC(12,6),
    sortino       NUMERIC(12,6),
    score         NUMERIC(16,8),
    selected      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (opt_id, candidate)
);

-- -------------------------------------------------------------------------
-- 滚动窗口寻优：滚动窗口 + 样本外验证
-- 每个策略的候选池在滑动窗口上重复寻优：训练窗（样本内）选参数，紧接的样本外窗用该参数跑，
-- 各折样本外段首尾相接拼成一条样本外净值 → 得到「诚实的样本外成绩」。
-- 一次滚动寻优 = 一个 opt_walk.opt_id，一折一行写进 opt_folds。
-- -------------------------------------------------------------------------
-- train_* = 该折训练窗（样本内）指标；oos_* = 该折紧接的样本外窗指标；
-- 两组口径与 opt_results 的同名指标完全一致（同一套 compute_metrics）。
CREATE TABLE IF NOT EXISTS opt_folds (
    opt_id         INT  NOT NULL,
    fold           INT  NOT NULL,
    is_opt_id      INT  NOT NULL,
    train_start    DATE NOT NULL,
    train_end      DATE NOT NULL,
    test_start     DATE NOT NULL,
    test_end       DATE NOT NULL,
    params         JSONB NOT NULL,
    -- 每折只存寻优真正用的 7 项：期末净值 + 年化/最大回撤/夏普/年化波动/卡玛/索提诺
    -- （窗口收益 ret_1d…ret_3y 在寻优里窗口太短大多为空、又从不参与打分，所以不落库）
    train_nav_end    NUMERIC(16,6),
    train_ret_annual NUMERIC(12,6),
    train_max_drawdown NUMERIC(12,6),
    train_sharpe     NUMERIC(12,6),
    train_annual_vol NUMERIC(12,6),
    train_calmar     NUMERIC(12,6),
    train_sortino    NUMERIC(12,6),
    train_score      NUMERIC(16,8),
    oos_nav_end      NUMERIC(16,6),
    oos_ret_annual   NUMERIC(12,6),
    oos_max_drawdown NUMERIC(12,6),
    oos_sharpe       NUMERIC(12,6),
    oos_annual_vol   NUMERIC(12,6),
    oos_calmar       NUMERIC(12,6),
    oos_sortino      NUMERIC(12,6),
    selected       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (opt_id, fold)
);

-- 一次滚动寻优的整体结论：折数/窗口配置（可复现）+ 晋级参数（共识参数）+ 样本外拼接指标。
CREATE TABLE IF NOT EXISTS opt_walk (
    opt_id          INT PRIMARY KEY,
    strategy_method TEXT   NOT NULL CHECK (strategy_method IN ('max_sharpe','min_variance','trend')),
    codes           TEXT[] NOT NULL,
    start_date      DATE,
    end_date        DATE,
    folds           INT    NOT NULL,
    train_days      INT    NOT NULL,
    test_days       INT    NOT NULL,
    step_days       INT    NOT NULL,
    winner_params   JSONB  NOT NULL,
    winner_folds    INT    NOT NULL,
    -- 样本外拼接净值的 7 项指标（与 opt_results / opt_folds 同口径）
    oos_nav_end      NUMERIC(16,6),
    oos_ret_annual   NUMERIC(12,6),
    oos_max_drawdown NUMERIC(12,6),
    oos_sharpe       NUMERIC(12,6),
    oos_annual_vol   NUMERIC(12,6),
    oos_calmar       NUMERIC(12,6),
    oos_sortino      NUMERIC(12,6),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 样本外拼接净值：把各折的样本外段首尾相接拼成一条净值（起点 1.0），一行一个交易日。
-- fold = 这一天来自第几折，页面据此画折分界竖线、悬停显示该折参数（看参数稳定性）。
-- 步长 < 样本外窗长时后一折会与前一折重叠，拼接只取「上一折结束之后」的交易日，
-- 并以该段实际保留下来的第一天重新归一 —— 所以曲线逐日唯一、不重复计同一段行情。
CREATE TABLE IF NOT EXISTS opt_oos_nav (
    opt_id      INT  NOT NULL,
    trade_date  DATE NOT NULL,
    nav         NUMERIC(16,8) NOT NULL,
    fold        INT  NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (opt_id, trade_date)
);

-- =========================================================================
-- 四、ETF 分红表
-- =========================================================================
CREATE TABLE IF NOT EXISTS etf_dividends (
    code          TEXT NOT NULL,
    ex_date       DATE NOT NULL,
    div_per_share NUMERIC(14,6) NOT NULL,
    source        TEXT NOT NULL DEFAULT 'imputed',
    note          TEXT,
    PRIMARY KEY (code, ex_date)
);

-- =========================================================================
-- 五、模拟账户表（= 资金池：一笔连续资金 + 账户级收益/风险指标回填列）
-- =========================================================================
CREATE TABLE IF NOT EXISTS sim_account (
    account_id     SERIAL PRIMARY KEY,
    account_name   TEXT,
    init_cash      NUMERIC(18,2) NOT NULL DEFAULT 1000000,
    start_date     DATE         NOT NULL,
    end_date       DATE,                      -- NULL = 跟最新交易日
    benchmark      TEXT NOT NULL DEFAULT 'sh510300',
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    note           TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    nav_end       NUMERIC(16,6),
    ret_total     NUMERIC(12,6),
    ret_annual    NUMERIC(12,6),
    ret_1d        NUMERIC(12,6),
    ret_1w        NUMERIC(12,6),
    ret_1m        NUMERIC(12,6),
    ret_3m        NUMERIC(12,6),
    ret_1y        NUMERIC(12,6),
    ret_2y        NUMERIC(12,6),
    ret_3y        NUMERIC(12,6),
    max_drawdown  NUMERIC(12,6),
    sharpe        NUMERIC(12,6),
    annual_vol    NUMERIC(12,6),
    calmar        NUMERIC(12,6),
    sortino       NUMERIC(12,6),
    -- 基准指标：与上面账户指标**逐项一一对应**（同 15 个、同顺序）
    bm_nav_end    NUMERIC(16,6),
    bm_ret_total  NUMERIC(12,6),
    bm_ret_annual NUMERIC(12,6),
    bm_ret_1d     NUMERIC(12,6),
    bm_ret_1w     NUMERIC(12,6),
    bm_ret_1m     NUMERIC(12,6),
    bm_ret_3m     NUMERIC(12,6),
    bm_ret_1y     NUMERIC(12,6),
    bm_ret_2y     NUMERIC(12,6),
    bm_ret_3y     NUMERIC(12,6),
    bm_max_drawdown NUMERIC(12,6),
    bm_sharpe     NUMERIC(12,6),
    bm_annual_vol NUMERIC(12,6),
    bm_calmar     NUMERIC(12,6),
    bm_sortino    NUMERIC(12,6),
    excess_total  NUMERIC(12,6),
    excess_annual NUMERIC(12,6),
    metrics_at    TIMESTAMPTZ
);

-- =========================================================================
-- 六、模拟策略分段表（账户内的策略：只放配置，不放指标）
-- =========================================================================
CREATE TABLE IF NOT EXISTS sim_params (
    account_id      INT  NOT NULL REFERENCES sim_account(account_id) ON DELETE CASCADE,
    account_name    TEXT,
    strategy_id     SERIAL PRIMARY KEY,
    strategy_name   TEXT,
    strategy_method TEXT NOT NULL CHECK (strategy_method IN ('max_sharpe', 'min_variance', 'trend')),
    period          INT,
    lookback        INT,
    min_w           NUMERIC(12,8),
    max_w           NUMERIC(12,8),
    long_window     INT,
    short_window    INT,
    codes           TEXT[]     NOT NULL,
    start_date      DATE       NOT NULL,
    end_date        DATE       NOT NULL,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (strategy_method IN ('max_sharpe', 'min_variance')
            AND lookback IS NOT NULL AND period IS NOT NULL
            AND min_w IS NOT NULL AND max_w IS NOT NULL)
        OR
        (strategy_method = 'trend'
            AND long_window IS NOT NULL AND short_window IS NOT NULL AND period IS NOT NULL)
    ),
    CHECK (min_w IS NULL OR min_w >= 0.0),
    CHECK (max_w IS NULL OR max_w <= 1.0),
    CHECK (min_w IS NULL OR max_w IS NULL OR min_w <= max_w),
    CHECK (short_window IS NULL OR long_window IS NULL OR short_window < long_window),
    CHECK (period IS NULL OR period > 0),
    UNIQUE NULLS NOT DISTINCT (account_id, strategy_method, period, lookback, min_w, max_w,
                               long_window, short_window, codes, start_date, end_date)
);

-- =========================================================================
-- 七、模拟结果表（账户维度 + 策略维度；sim_nav 为账户级净值）
-- =========================================================================
CREATE TABLE IF NOT EXISTS sim_nav (
    account_id     INT  NOT NULL REFERENCES sim_account(account_id) ON DELETE CASCADE,
    account_name   TEXT,
    trade_date     DATE NOT NULL,
    nav            NUMERIC(16,6),
    cash           NUMERIC(18,2),
    market_value   NUMERIC(18,2),
    dividend       NUMERIC(18,2) NOT NULL DEFAULT 0,
    total          NUMERIC(18,2),
    PRIMARY KEY (account_id, trade_date)
);
CREATE TABLE IF NOT EXISTS sim_calendar (
    account_id     INT  NOT NULL REFERENCES sim_account(account_id) ON DELETE CASCADE,
    account_name   TEXT,
    strategy_id    INT  NOT NULL REFERENCES sim_params(strategy_id) ON DELETE CASCADE,
    strategy_name  TEXT,
    rebal_date     DATE NOT NULL,
    signal_date    DATE NOT NULL,
    PRIMARY KEY (account_id, strategy_id, rebal_date)
);
CREATE TABLE IF NOT EXISTS sim_trades (
    account_id     INT  NOT NULL REFERENCES sim_account(account_id) ON DELETE CASCADE,
    account_name   TEXT,
    strategy_id    INT  NOT NULL REFERENCES sim_params(strategy_id) ON DELETE CASCADE,
    strategy_name  TEXT,
    trade_date     DATE NOT NULL,
    code           TEXT NOT NULL,
    name           TEXT,
    side           TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    qty            BIGINT NOT NULL,
    price          NUMERIC(14,4),
    amount         NUMERIC(18,2),
    fee            NUMERIC(14,2),
    PRIMARY KEY (account_id, strategy_id, trade_date, code, side)
);
CREATE TABLE IF NOT EXISTS sim_holdings (
    account_id     INT  NOT NULL REFERENCES sim_account(account_id) ON DELETE CASCADE,
    account_name   TEXT,
    strategy_id    INT  NOT NULL REFERENCES sim_params(strategy_id) ON DELETE CASCADE,
    strategy_name  TEXT,
    rebal_date     DATE NOT NULL,
    code           TEXT NOT NULL,
    name           TEXT,
    weight         NUMERIC(12,8),
    signal_close   NUMERIC(14,4),
    raw_close      NUMERIC(14,4),
    signal_equity  NUMERIC(18,2),
    target_qty     BIGINT,
    PRIMARY KEY (account_id, strategy_id, rebal_date, code)
);
CREATE TABLE IF NOT EXISTS sim_benchmark_nav (
    account_id     INT  NOT NULL REFERENCES sim_account(account_id) ON DELETE CASCADE,
    account_name   TEXT,
    benchmark      TEXT NOT NULL,
    trade_date     DATE NOT NULL,
    nav            NUMERIC(16,6),
    PRIMARY KEY (account_id, trade_date)
);


-- =========================================================================
-- 七、表 / 列注释（自文档化，psql 里 \d+ 或 PGAdmin 可见；幂等可重复执行）
-- =========================================================================

-- ---------- 〇、行情三表 ----------
COMMENT ON TABLE trade_calendar IS '交易日历：交易日/是否开盘（etf_sync.py 维护，三框架只读）';
COMMENT ON COLUMN trade_calendar.cal_date IS '日历日期';
COMMENT ON COLUMN trade_calendar.is_open IS '是否开盘（交易日）';
COMMENT ON COLUMN trade_calendar.updated_at IS '更新时间';

COMMENT ON TABLE etf_bars_raw IS 'ETF 不复权日线行情（分红除息日会有跳空）';
COMMENT ON TABLE etf_bars_hfq IS 'ETF 后复权日线行情（隐含分红再投资=总回报口径）';
COMMENT ON COLUMN etf_bars_raw.code IS 'ETF 代码（带 sh/sz 前缀）';
COMMENT ON COLUMN etf_bars_hfq.code IS 'ETF 代码（带 sh/sz 前缀）';
COMMENT ON COLUMN etf_bars_raw.name IS 'ETF 名称';
COMMENT ON COLUMN etf_bars_hfq.name IS 'ETF 名称';
COMMENT ON COLUMN etf_bars_raw.trade_date IS '交易日';
COMMENT ON COLUMN etf_bars_hfq.trade_date IS '交易日';
COMMENT ON COLUMN etf_bars_raw.open IS '开盘价';
COMMENT ON COLUMN etf_bars_raw.high IS '最高价';
COMMENT ON COLUMN etf_bars_raw.low IS '最低价';
COMMENT ON COLUMN etf_bars_raw.close IS '收盘价';
COMMENT ON COLUMN etf_bars_raw.volume IS '成交量（份）';

-- ---------- 一、回测参数表 bt_params（= 策略表）----------
COMMENT ON TABLE bt_params IS '回测参数表=策略表：一行=一个策略（方法+参数+codes），指标/基准由回测框架回填';
COMMENT ON COLUMN bt_params.strategy_id IS '策略编码（自增主键）';
COMMENT ON COLUMN bt_params.strategy_name IS '策略名称（标签，不参与去重）';
COMMENT ON COLUMN bt_params.strategy_method IS '构建策略的方法：max_sharpe/min_variance/trend';
COMMENT ON COLUMN bt_params.period IS '调仓周期（交易日数）';
COMMENT ON COLUMN bt_params.lookback IS '回看窗口（交易日数，最大夏普/最小方差用）';
COMMENT ON COLUMN bt_params.min_w IS '单只 ETF 最小权重';
COMMENT ON COLUMN bt_params.max_w IS '单只 ETF 最大权重';
COMMENT ON COLUMN bt_params.long_window IS '长期回看窗口（趋势轮动用）';
COMMENT ON COLUMN bt_params.short_window IS '短期回看窗口（趋势轮动用，需小于长期）';
COMMENT ON COLUMN bt_params.codes IS '组合成分代码数组（写入前排序）';
COMMENT ON COLUMN bt_params.start_date IS '回测起始日';
COMMENT ON COLUMN bt_params.end_date IS '回测截止日（NULL = 自动跟到行情最新交易日）';
COMMENT ON COLUMN bt_params.source IS '来源：manual 人工 / optimize 寻优';
COMMENT ON COLUMN bt_params.opt_id IS '来源为 optimize 时，来自哪一次寻优';
COMMENT ON COLUMN bt_params.is_active IS '是否有效：TRUE 回测跑；FALSE 触发寻优';
COMMENT ON COLUMN bt_params.note IS '备注';
COMMENT ON COLUMN bt_params.created_at IS '创建时间';
COMMENT ON COLUMN bt_params.nav_end IS '期末净值';
COMMENT ON COLUMN bt_params.ret_total IS '区间累计收益（存小数，0.1321=13.21%）';
COMMENT ON COLUMN bt_params.ret_annual IS '区间年化收益';
COMMENT ON COLUMN bt_params.ret_1d IS '期末当日涨跌幅';
COMMENT ON COLUMN bt_params.ret_1w IS '近一周收益（5 交易日）';
COMMENT ON COLUMN bt_params.ret_1m IS '近一月收益（21 交易日）';
COMMENT ON COLUMN bt_params.ret_3m IS '近三月收益（63 交易日）';
COMMENT ON COLUMN bt_params.ret_1y IS '近一年收益（252 交易日）';
COMMENT ON COLUMN bt_params.ret_2y IS '近两年收益（504 交易日）';
COMMENT ON COLUMN bt_params.ret_3y IS '近三年收益（756 交易日）';
COMMENT ON COLUMN bt_params.max_drawdown IS '最大回撤（存负数，-0.2687=-26.87%）';
COMMENT ON COLUMN bt_params.sharpe IS '年化夏普（无风险利率取 0）';
COMMENT ON COLUMN bt_params.annual_vol IS '年化波动率';
COMMENT ON COLUMN bt_params.calmar IS '卡玛比率 = 年化收益 / |最大回撤|（回撤为 0 记 0）';
COMMENT ON COLUMN bt_params.sortino IS '索提诺比率 = 年化收益 / 年化下行波动（下行波动只算负收益离差，分母取全部交易日）';
COMMENT ON COLUMN bt_params.benchmark IS '基准 ETF 代码（默认 sh510300）';
COMMENT ON COLUMN bt_params.bm_nav_end IS '基准期末净值（起点 1.0）';
COMMENT ON COLUMN bt_params.bm_ret_total IS '基准累计收益';
COMMENT ON COLUMN bt_params.bm_ret_annual IS '基准年化收益';
COMMENT ON COLUMN bt_params.bm_ret_1d IS '基准期末当日涨跌幅';
COMMENT ON COLUMN bt_params.bm_ret_1w IS '基准近一周收益（5 交易日）';
COMMENT ON COLUMN bt_params.bm_ret_1m IS '基准近一月收益（21 交易日）';
COMMENT ON COLUMN bt_params.bm_ret_3m IS '基准近三月收益（63 交易日）';
COMMENT ON COLUMN bt_params.bm_ret_1y IS '基准近一年收益（252 交易日）';
COMMENT ON COLUMN bt_params.bm_ret_2y IS '基准近两年收益（504 交易日）';
COMMENT ON COLUMN bt_params.bm_ret_3y IS '基准近三年收益（756 交易日）';
COMMENT ON COLUMN bt_params.bm_max_drawdown IS '基准最大回撤';
COMMENT ON COLUMN bt_params.bm_sharpe IS '基准夏普';
COMMENT ON COLUMN bt_params.bm_annual_vol IS '基准年化波动率';
COMMENT ON COLUMN bt_params.bm_calmar IS '基准卡玛比率';
COMMENT ON COLUMN bt_params.bm_sortino IS '基准索提诺比率';
COMMENT ON COLUMN bt_params.excess_total IS '超额累计收益 = 策略累计 - 基准累计';
COMMENT ON COLUMN bt_params.excess_annual IS '超额年化收益 = 策略年化 - 基准年化';
COMMENT ON COLUMN bt_params.metrics_at IS '指标计算时间（判断是否最新回测的依据）';

-- ---------- 二、回测结果表 5 张 ----------
COMMENT ON TABLE bt_calendar IS '策略调仓日历：调仓日 + 对应信号日；含未来待执行调仓日（rebal_date > 最新行情日）';
COMMENT ON COLUMN bt_calendar.strategy_name IS '策略名称（冗余展示，免 JOIN；必须与 bt_params 保持一致，改名后要同步）';
COMMENT ON COLUMN bt_calendar.rebal_date IS '调仓日';
COMMENT ON COLUMN bt_calendar.signal_date IS '信号日（调仓日前一交易日）';

COMMENT ON TABLE bt_trades IS '策略交易明细（后复权口径）';
COMMENT ON COLUMN bt_trades.trade_date IS '成交日（=调仓日）';
COMMENT ON COLUMN bt_trades.side IS '买卖方向：BUY/SELL';
COMMENT ON COLUMN bt_trades.qty IS '成交数量（份）';
COMMENT ON COLUMN bt_trades.price IS '成交价（后复权收盘价）';
COMMENT ON COLUMN bt_trades.amount IS '成交金额（不含费用）';
COMMENT ON COLUMN bt_trades.fee IS '手续费（万五）';

COMMENT ON TABLE bt_nav IS '策略每日净值（后复权口径）';
COMMENT ON COLUMN bt_nav.nav IS '净值 = 总资产/初始资金（起点 1.0）';
COMMENT ON COLUMN bt_nav.cash IS '现金';
COMMENT ON COLUMN bt_nav.market_value IS '持仓市值';
COMMENT ON COLUMN bt_nav.total IS '总资产（现金+市值）';

COMMENT ON TABLE bt_holdings IS '策略调仓日成分明细（权重/信号价/目标数量）';
COMMENT ON COLUMN bt_holdings.rebal_date IS '调仓日';
COMMENT ON COLUMN bt_holdings.weight IS '目标权重';
COMMENT ON COLUMN bt_holdings.signal_close IS '信号日后复权收盘价';
COMMENT ON COLUMN bt_holdings.signal_equity IS '信号日总资产';
COMMENT ON COLUMN bt_holdings.target_qty IS '目标持仓数量（按 100 份取整）';

COMMENT ON TABLE bt_benchmark_nav IS '回测基准逐日净值（基准ETF后复权买入持有，起点 1.0）';
COMMENT ON COLUMN bt_benchmark_nav.benchmark IS '基准 ETF 代码';

-- ---------- 三、寻优结果表 ----------
COMMENT ON TABLE opt_results IS '寻优结果：每组候选参数的各指标 + 综合分 + 是否被选中';
COMMENT ON COLUMN opt_results.opt_id IS '一次分组寻优的编号（同 method+codes+起止 的一批候选共享）';
COMMENT ON COLUMN opt_results.candidate IS '候选序号 1..N';
COMMENT ON COLUMN opt_results.strategy_method IS '构建策略的方法';
COMMENT ON COLUMN opt_results.params IS '该候选的参数组合（JSONB）';
COMMENT ON COLUMN opt_results.score IS '综合分 = 年化/卡玛/夏普 组内排名百分位的等权平均（0~1，越大越好）';
COMMENT ON COLUMN opt_results.selected IS '是否被选中（本批综合分最高，每组只选中 1 个）';
COMMENT ON COLUMN opt_results.codes IS '候选池 ETF 代码';
COMMENT ON COLUMN opt_results.start_date IS '回测起始日';
COMMENT ON COLUMN opt_results.end_date IS '回测结束日';
COMMENT ON COLUMN opt_results.nav_end IS '期末净值（起点 1.0）';
COMMENT ON COLUMN opt_results.ret_annual IS '区间年化收益（综合分三个分量之一）';
COMMENT ON COLUMN opt_results.max_drawdown IS '最大回撤（存负数，-0.2687=-26.87%）';
COMMENT ON COLUMN opt_results.sharpe IS '年化夏普（无风险利率取 0）';
COMMENT ON COLUMN opt_results.annual_vol IS '年化波动率';
COMMENT ON COLUMN opt_results.calmar IS '卡玛比率 = 年化收益 / |最大回撤|（回撤为 0 记 0；综合分三个分量之一）';
COMMENT ON COLUMN opt_results.sortino IS '索提诺比率 = 年化收益 / 年化下行波动（下行波动只算负收益离差，分母取全部交易日）';
COMMENT ON COLUMN opt_results.created_at IS '创建时间';

-- ---------- 三之附：滚动窗口寻优（opt_folds / opt_walk）----------
COMMENT ON TABLE opt_folds IS '滚动窗口寻优的每折明细：训练窗选参数 + 紧接样本外窗验证；train_*/oos_* 指标口径与 opt_results 同名指标一致';
COMMENT ON COLUMN opt_folds.opt_id IS '所属滚动寻优批次（= opt_walk.opt_id）';
COMMENT ON COLUMN opt_folds.fold IS '折序号 1..N（按时间从早到晚）';
COMMENT ON COLUMN opt_folds.is_opt_id IS '该折训练窗那批候选在 opt_results 里的 opt_id（每折各一批，互不覆盖）';
COMMENT ON COLUMN opt_folds.train_start IS '训练窗（样本内）起始日';
COMMENT ON COLUMN opt_folds.train_end IS '训练窗结束日';
COMMENT ON COLUMN opt_folds.test_start IS '样本外窗起始日（紧接训练窗，不留空档）';
COMMENT ON COLUMN opt_folds.test_end IS '样本外窗结束日';
COMMENT ON COLUMN opt_folds.params IS '该折在训练窗上按综合分选出的参数（JSONB）';
COMMENT ON COLUMN opt_folds.train_score IS '该折胜出参数在训练窗上的综合分（0~1）';
COMMENT ON COLUMN opt_folds.selected IS '该折胜出参数是不是最终晋级参数（共识参数被哪几折选中）';
COMMENT ON TABLE opt_walk IS '滚动窗口寻优批次汇总：折数与窗口配置（可复现）+ 晋级用的共识参数 + 样本外拼接指标';
COMMENT ON COLUMN opt_walk.strategy_method IS '构建策略的方法';
COMMENT ON COLUMN opt_walk.codes IS '候选池 ETF 代码';
COMMENT ON COLUMN opt_walk.start_date IS '整段寻优起始日';
COMMENT ON COLUMN opt_walk.end_date IS '整段寻优结束日（NULL = 跟最新行情）';
COMMENT ON COLUMN opt_walk.folds IS '折数';
COMMENT ON COLUMN opt_walk.train_days IS '训练窗长度（交易日）';
COMMENT ON COLUMN opt_walk.test_days IS '样本外窗长度（交易日）';
COMMENT ON COLUMN opt_walk.step_days IS '滚动步长（交易日）';
COMMENT ON COLUMN opt_walk.winner_params IS '晋级参数 = 参数级稳定性投票的结果：每折给每个参数取值记名次（该取值在这一折里最靠前的候选排第几），跨折取平均名次最好的取值拼成稳定组合，再投影到真实候选里逐项匹配最多的那一套（保证真实出现过）';
COMMENT ON COLUMN opt_walk.winner_folds IS '晋级参数里最弱参数「进过前 10% 候选」的折数，即支撑度下界（如 19 = 共 20 折，其中 19 折的前 10% 出现过它）；只作可信度参考，不参与挑选';
COMMENT ON COLUMN opt_walk.oos_ret_annual IS '样本外拼接净值算出的年化收益（各折样本外段首尾相接）';
COMMENT ON COLUMN opt_walk.oos_nav_end IS '样本外拼接净值终值（起点 1.0）';
COMMENT ON COLUMN opt_walk.oos_max_drawdown IS '样本外拼接净值最大回撤';
COMMENT ON COLUMN opt_walk.oos_sharpe IS '样本外拼接净值年化夏普';
COMMENT ON COLUMN opt_walk.oos_calmar IS '样本外拼接净值卡玛比率';
COMMENT ON COLUMN opt_walk.oos_sortino IS '样本外拼接净值索提诺比率';
COMMENT ON COLUMN opt_walk.oos_annual_vol IS '样本外拼接净值年化波动率';
COMMENT ON COLUMN opt_walk.created_at IS '创建时间';
COMMENT ON TABLE opt_oos_nav IS '滚动寻优的样本外拼接净值：一行一个交易日；把各折样本外段首尾相接拼成一条线，用来直观看参数换了以后曲线怎么走';
COMMENT ON COLUMN opt_oos_nav.opt_id IS '所属滚动寻优批次（= opt_walk.opt_id）';
COMMENT ON COLUMN opt_oos_nav.trade_date IS '交易日（整条曲线逐日唯一、无重复）';
COMMENT ON COLUMN opt_oos_nav.nav IS '拼接净值（各折样本外段以起点 1.0 归一后接续，整条曲线起点 1.0）';
COMMENT ON COLUMN opt_oos_nav.fold IS '该交易日来自第几折（页面画折分界竖线、悬停显示该折参数）';

-- ---------- 四、分红表 ----------
COMMENT ON TABLE etf_dividends IS 'ETF 每份现金分红（自动反推生成，可人工校正）';
COMMENT ON COLUMN etf_dividends.ex_date IS '除息日';
COMMENT ON COLUMN etf_dividends.div_per_share IS '每份派息（元）';
COMMENT ON COLUMN etf_dividends.source IS 'imputed 自动反推 / manual 人工修正';

-- ---------- 五、模拟账户表 sim_account（= 资金池）----------
COMMENT ON TABLE sim_account IS '模拟账户=一笔连续资金：账户级收益/风险指标（模拟框架回填），账户内含多个策略分段';
COMMENT ON COLUMN sim_account.account_id IS '账户编码（自增主键）';
COMMENT ON COLUMN sim_account.account_name IS '账户名称';
COMMENT ON COLUMN sim_account.init_cash IS '账户初始资金（真实元，默认 100 万）';
COMMENT ON COLUMN sim_account.start_date IS '账户整体模拟起始日';
COMMENT ON COLUMN sim_account.end_date IS '账户整体模拟截止日（NULL = 自动跟到最新交易日）';
COMMENT ON COLUMN sim_account.benchmark IS '基准 ETF 代码（默认 sh510300）';
COMMENT ON COLUMN sim_account.is_active IS '是否有效：TRUE 模拟；FALSE 跳过';
COMMENT ON COLUMN sim_account.note IS '备注';
COMMENT ON COLUMN sim_account.created_at IS '创建时间';
COMMENT ON COLUMN sim_account.nav_end IS '期末净值（账户级）';
COMMENT ON COLUMN sim_account.ret_total IS '区间累计收益（存小数，0.1321=13.21%）';
COMMENT ON COLUMN sim_account.ret_annual IS '区间年化收益';
COMMENT ON COLUMN sim_account.ret_1d IS '期末当日涨跌幅';
COMMENT ON COLUMN sim_account.ret_1w IS '近一周收益（5 交易日）';
COMMENT ON COLUMN sim_account.ret_1m IS '近一月收益（21 交易日）';
COMMENT ON COLUMN sim_account.ret_3m IS '近三月收益（63 交易日）';
COMMENT ON COLUMN sim_account.ret_1y IS '近一年收益（252 交易日）';
COMMENT ON COLUMN sim_account.ret_2y IS '近两年收益（504 交易日）';
COMMENT ON COLUMN sim_account.ret_3y IS '近三年收益（756 交易日）';
COMMENT ON COLUMN sim_account.max_drawdown IS '账户最大回撤（存负数）';
COMMENT ON COLUMN sim_account.sharpe IS '账户年化夏普（无风险利率取 0）';
COMMENT ON COLUMN sim_account.annual_vol IS '账户年化波动率';
COMMENT ON COLUMN sim_account.calmar IS '卡玛比率 = 年化收益 / |最大回撤|（回撤为 0 记 0）';
COMMENT ON COLUMN sim_account.sortino IS '索提诺比率 = 年化收益 / 年化下行波动（下行波动只算负收益离差，分母取全部交易日）';
COMMENT ON COLUMN sim_account.bm_nav_end IS '基准期末净值（起点 1.0）';
COMMENT ON COLUMN sim_account.bm_ret_total IS '基准累计收益';
COMMENT ON COLUMN sim_account.bm_ret_annual IS '基准年化收益';
COMMENT ON COLUMN sim_account.bm_ret_1d IS '基准期末当日涨跌幅';
COMMENT ON COLUMN sim_account.bm_ret_1w IS '基准近一周收益（5 交易日）';
COMMENT ON COLUMN sim_account.bm_ret_1m IS '基准近一月收益（21 交易日）';
COMMENT ON COLUMN sim_account.bm_ret_3m IS '基准近三月收益（63 交易日）';
COMMENT ON COLUMN sim_account.bm_ret_1y IS '基准近一年收益（252 交易日）';
COMMENT ON COLUMN sim_account.bm_ret_2y IS '基准近两年收益（504 交易日）';
COMMENT ON COLUMN sim_account.bm_ret_3y IS '基准近三年收益（756 交易日）';
COMMENT ON COLUMN sim_account.bm_max_drawdown IS '基准最大回撤';
COMMENT ON COLUMN sim_account.bm_sharpe IS '基准夏普';
COMMENT ON COLUMN sim_account.bm_annual_vol IS '基准年化波动率';
COMMENT ON COLUMN sim_account.bm_calmar IS '基准卡玛比率';
COMMENT ON COLUMN sim_account.bm_sortino IS '基准索提诺比率';
COMMENT ON COLUMN sim_account.excess_total IS '超额累计收益 = 账户累计 - 基准累计';
COMMENT ON COLUMN sim_account.excess_annual IS '超额年化收益 = 账户年化 - 基准年化';
COMMENT ON COLUMN sim_account.metrics_at IS '指标计算时间';

-- ---------- 六、模拟策略分段表 sim_params（账户内策略）----------
COMMENT ON TABLE sim_params IS '模拟策略分段表：账户内的一段策略（方法+参数+codes），只放配置不放指标';
COMMENT ON COLUMN sim_params.account_id IS '所属账户编码';
COMMENT ON COLUMN sim_params.account_name IS '账户名称（冗余展示，免 JOIN）';
COMMENT ON COLUMN sim_params.strategy_id IS '策略分段编码（自增主键）';
COMMENT ON COLUMN sim_params.strategy_name IS '分段名称（标签）';
COMMENT ON COLUMN sim_params.strategy_method IS '构建策略的方法：max_sharpe/min_variance/trend';
COMMENT ON COLUMN sim_params.period IS '调仓周期（交易日数）';
COMMENT ON COLUMN sim_params.lookback IS '回看窗口（交易日数，最大夏普/最小方差用）';
COMMENT ON COLUMN sim_params.min_w IS '单只 ETF 最小权重';
COMMENT ON COLUMN sim_params.max_w IS '单只 ETF 最大权重';
COMMENT ON COLUMN sim_params.long_window IS '长期回看窗口（趋势轮动用）';
COMMENT ON COLUMN sim_params.short_window IS '短期回看窗口（趋势轮动用）';
COMMENT ON COLUMN sim_params.codes IS '本段组合成分代码数组（写入前排序）';
COMMENT ON COLUMN sim_params.start_date IS '本段生效起始日（账户时间线内，接上一段）';
COMMENT ON COLUMN sim_params.end_date IS '本段生效截止日（改小 = 提前结束；过了这天的日子该段不参与，只持股估值）';
COMMENT ON COLUMN sim_params.note IS '备注';

-- ---------- 七、模拟结果表（账户维度 + 策略维度）----------
COMMENT ON TABLE sim_nav IS '模拟账户每日净值（真实货币口径，账户级连续）';
COMMENT ON COLUMN sim_nav.account_id IS '账户编码';
COMMENT ON COLUMN sim_nav.account_name IS '账户名称（冗余展示）';
COMMENT ON COLUMN sim_nav.nav IS '净值 = 总资产/账户初始资金（起点 1.0）';
COMMENT ON COLUMN sim_nav.cash IS '现金（真实元）';
COMMENT ON COLUMN sim_nav.market_value IS '持仓市值（真实元）';
COMMENT ON COLUMN sim_nav.dividend IS '当日现金分红入账（真实元）';
COMMENT ON COLUMN sim_nav.total IS '账户总资产（真实元）';

COMMENT ON TABLE sim_calendar IS '账户各策略分段调仓日历；含未来待执行调仓日（rebal_date > 最新行情日）';
COMMENT ON COLUMN sim_calendar.account_id IS '账户编码';
COMMENT ON COLUMN sim_calendar.strategy_id IS '策略分段编码';
COMMENT ON COLUMN sim_calendar.rebal_date IS '调仓日';
COMMENT ON COLUMN sim_calendar.signal_date IS '信号日（调仓日前一交易日）';

COMMENT ON TABLE sim_trades IS '账户各策略分段交易明细（真实价）';
COMMENT ON COLUMN sim_trades.account_id IS '账户编码';
COMMENT ON COLUMN sim_trades.strategy_id IS '策略分段编码';
COMMENT ON COLUMN sim_trades.trade_date IS '成交日（=调仓日）';
COMMENT ON COLUMN sim_trades.side IS '买卖方向：BUY/SELL';
COMMENT ON COLUMN sim_trades.qty IS '成交数量（份）';
COMMENT ON COLUMN sim_trades.price IS '真实成交价（不复权收盘价）';
COMMENT ON COLUMN sim_trades.amount IS '真实成交金额（不含费用）';
COMMENT ON COLUMN sim_trades.fee IS '真实手续费（万五）';

COMMENT ON TABLE sim_holdings IS '账户各策略分段调仓日成分明细';
COMMENT ON COLUMN sim_holdings.account_id IS '账户编码';
COMMENT ON COLUMN sim_holdings.strategy_id IS '策略分段编码';
COMMENT ON COLUMN sim_holdings.weight IS '目标权重';
COMMENT ON COLUMN sim_holdings.signal_close IS '信号日后复权收盘价（算权重用）';
COMMENT ON COLUMN sim_holdings.raw_close IS '信号日真实收盘价（算目标数量用）';
COMMENT ON COLUMN sim_holdings.signal_equity IS '信号日账户真实总资产';
COMMENT ON COLUMN sim_holdings.target_qty IS '目标持仓数量（按 100 份取整）';

COMMENT ON TABLE sim_benchmark_nav IS '模拟账户基准逐日净值（真实货币买入持有 + 分红入现金）';
COMMENT ON COLUMN sim_benchmark_nav.account_id IS '账户编码';
COMMENT ON COLUMN sim_benchmark_nav.benchmark IS '基准 ETF 代码';