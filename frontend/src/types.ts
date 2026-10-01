// 与 FastAPI 返回 JSON 一一对应

export interface CodeItem {
  code: string
  name: string
}

// 指标 20 列（策略净值 + 基准 + 超额；收益/回撤为小数，0.1321 = 13.21%）
export interface Metric {
  nav_end: number
  ret_total: number
  ret_annual: number
  ret_1d: number
  ret_1w: number
  ret_1m: number
  ret_3m: number
  ret_1y: number
  ret_2y: number
  ret_3y: number
  max_drawdown: number
  sharpe: number
  annual_vol: number
  calmar: number
  sortino: number
  // 基准指标：与上面策略/账户指标逐项一一对应（同 15 个、同顺序）
  bm_nav_end: number
  bm_ret_total: number
  bm_ret_annual: number
  bm_ret_1d: number
  bm_ret_1w: number
  bm_ret_1m: number
  bm_ret_3m: number
  bm_ret_1y: number
  bm_ret_2y: number
  bm_ret_3y: number
  bm_max_drawdown: number
  bm_sharpe: number
  bm_annual_vol: number
  bm_calmar: number
  bm_sortino: number
  excess_total: number
  excess_annual: number
}

export type Method = 'max_sharpe' | 'min_variance' | 'trend'
export type Side = 'BUY' | 'SELL'
export type TaskStatus = 'queued' | 'running' | 'done' | 'error'

export type Strategy = {
  strategy_id: number
  strategy_name: string | null
  strategy_method: Method
  period: number | null
  lookback: number | null
  min_w: number | null
  max_w: number | null
  long_window: number | null
  short_window: number | null
  codes: string[]
  start_date: string
  end_date: string | null                 // NULL = 跟最新行情交易日
  benchmark: string
  source: string | null
  opt_id: number | null
  is_active: boolean
  note: string | null
  metrics_at: string | null
} & Partial<Metric>

export type SimAccount = {
  account_id: number
  account_name: string | null
  init_cash: number
  start_date: string
  end_date: string | null
  benchmark: string
  is_active: boolean
  note: string | null
  created_at: string
  metrics_at: string | null
} & Partial<Metric>

export interface SimSegment {
  strategy_id: number
  account_id: number
  account_name: string | null
  strategy_name: string | null
  strategy_method: Method
  period: number | null
  lookback: number | null
  min_w: number | null
  max_w: number | null
  long_window: number | null
  short_window: number | null
  codes: string[]
  start_date: string
  end_date: string
  note: string | null
  created_at: string
}

export interface CalendarPoint {
  rebal_date: string
  signal_date: string
  is_future?: boolean
  strategy_id?: number
  strategy_name?: string
}

export interface CurvePoint {
  trade_date: string
  nav: number
  benchmark_nav: number
}

export interface DrawdownPoint {
  trade_date: string
  strategy_dd: number
  benchmark_dd: number
}

// 逐日涨跌幅（后端按相邻两个交易日净值算好，与业绩走势同源；首日不产出）
export interface ReturnPoint {
  trade_date: string
  ret: number | null
  benchmark_ret: number | null
}

export interface Holding {
  rebal_date: string
  code: string
  name: string
  weight: number
  target_qty: number
  signal_close: number
  signal_equity?: number
  raw_close?: number
  strategy_id?: number
  strategy_name?: string
}

export interface Trade {
  trade_date: string
  code: string
  name: string
  side: Side
  qty: number
  price: number
  amount: number
  fee: number
  strategy_id?: number
  strategy_name?: string
}

// 寻优胜出参数（一次寻优一行，来自 /optimize/winners）
// basis='oos'：滚动窗口寻优，指标是**样本外拼接**口径，folds/winner_folds 有值；
// basis='full'：旧方法或样本太短退回的单段寻优，指标是**全样本**口径，折数为 null。
export interface OptWinner {
  opt_id: number
  strategy_method: Method
  codes: string[]
  start_date: string
  end_date: string | null
  params: Record<string, number>
  folds: number | null
  winner_folds: number | null
  train_days: number | null
  test_days: number | null
  step_days: number | null
  basis: 'oos' | 'full'
  strategy_name?: string | null
  // 指标只有 7 项（与 opt_* 表落库口径一致）：期末净值 + 年化/最大回撤/夏普/年化波动/卡玛/索提诺
  nav_end?: number
  ret_annual?: number
  max_drawdown?: number
  sharpe?: number
  annual_vol?: number
  calmar?: number
  sortino?: number
  score: number | null
}

// 滚动寻优的每折明细（/optimize/walk/{opt_id}/folds）：train_* 训练窗、oos_* 样本外窗，
// 两组口径与 opt_results 同名指标一致（同一套 compute_metrics），各只落 7 项指标
export interface OptFold {
  opt_id: number
  fold: number
  is_opt_id: number
  train_start: string
  train_end: string
  test_start: string
  test_end: string
  params: Record<string, number>
  train_nav_end?: number | null
  train_ret_annual?: number | null
  train_max_drawdown?: number | null
  train_sharpe?: number | null
  train_annual_vol?: number | null
  train_calmar?: number | null
  train_sortino?: number | null
  train_score?: number | null
  oos_nav_end?: number | null
  oos_ret_annual?: number | null
  oos_max_drawdown?: number | null
  oos_sharpe?: number | null
  oos_annual_vol?: number | null
  oos_calmar?: number | null
  oos_sortino?: number | null
  // 该折胜出参数是否恰好等于最终晋级的参数（晋级参数是投影出来的，可能全为 false）
  selected: boolean
}

// 样本外拼接净值曲线（/optimize/walk/{opt_id}/oos）：一行一个交易日，起点 1.0。
// fold = 该日来自第几折 —— 页面据此画折分界竖线、悬停显示该折参数
export interface OosPoint {
  trade_date: string
  nav: number
  fold: number
}

export interface Task {
  task_id: string
  name: string
  status: TaskStatus
  log: string
  started_at: string | null
  finished_at: string | null
}