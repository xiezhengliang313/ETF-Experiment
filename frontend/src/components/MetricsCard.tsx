import { Card, Col, Row, Table } from 'antd'
import type { TableColumnsType } from 'antd'
import type { Metric } from '../types'

const RED = '#cf1322'
const GREEN = '#3f8600'
const DARK = '#1f1f1f'
const MUTED = '#bbb'

const pct = (v: any) => (v == null ? '—' : `${(v * 100).toFixed(2)}%`)
const num3 = (v: any) => (v == null ? '—' : Number(v).toFixed(3))

type Kind = 'ret' | 'risk'

// 收益类：正红负绿；风险类：中性深色（回撤恒为负，上绿色会误导）
const colorOf = (v: any, kind: Kind): string => {
  if (kind === 'risk' || v == null) return DARK
  return v > 0 ? RED : v < 0 ? GREEN : DARK
}

type Getter = (m: Partial<Metric>) => number | null
const col = (k: keyof Metric): Getter => (m) => (m[k] ?? null) as number | null

type Field = { label: string; fmt: (v: any) => string; kind: Kind; v: Getter; b: Getter }

// 拆两半是为了填满页面宽度：单表铺满整屏时每列 400px、只有一个数字，太稀。
// 左 7 行 + 右 7 行 = 14，与库里「策略/账户指标块 ↔ 基准指标块」一一对应（各 15 项里去掉期末净值）。
// 期末净值不进表：它是水平值不是比率，和基准相减没有意义，且等于「全期收益」的另一种写法
// （净值 = 1 + 全期收益）；净值走势在「走势图」页签。
// 卡玛比率、索提诺比率都由框架 compute_metrics 算好入库（bt_params / sim_account），前端只读。
const LEFT: Field[] = [
  { label: '当日', fmt: pct, kind: 'ret', v: col('ret_1d'), b: col('bm_ret_1d') },
  { label: '近一周', fmt: pct, kind: 'ret', v: col('ret_1w'), b: col('bm_ret_1w') },
  { label: '近一月', fmt: pct, kind: 'ret', v: col('ret_1m'), b: col('bm_ret_1m') },
  { label: '近三月', fmt: pct, kind: 'ret', v: col('ret_3m'), b: col('bm_ret_3m') },
  { label: '近一年', fmt: pct, kind: 'ret', v: col('ret_1y'), b: col('bm_ret_1y') },
  { label: '近两年', fmt: pct, kind: 'ret', v: col('ret_2y'), b: col('bm_ret_2y') },
  { label: '近三年', fmt: pct, kind: 'ret', v: col('ret_3y'), b: col('bm_ret_3y') },
]

const RIGHT: Field[] = [
  { label: '全期收益', fmt: pct, kind: 'ret', v: col('ret_total'), b: col('bm_ret_total') },
  { label: '年化收益', fmt: pct, kind: 'ret', v: col('ret_annual'), b: col('bm_ret_annual') },
  { label: '最大回撤', fmt: pct, kind: 'risk', v: col('max_drawdown'), b: col('bm_max_drawdown') },
  { label: '夏普', fmt: num3, kind: 'risk', v: col('sharpe'), b: col('bm_sharpe') },
  { label: '年化波动', fmt: pct, kind: 'risk', v: col('annual_vol'), b: col('bm_annual_vol') },
  { label: '卡玛比率', fmt: num3, kind: 'risk', v: col('calmar'), b: col('bm_calmar') },
  { label: '索提诺比率', fmt: num3, kind: 'risk', v: col('sortino'), b: col('bm_sortino') },
]

type MetricRow = Field & { val: number | null; base: number | null }

const COLS: TableColumnsType<MetricRow> = [
  { title: '指标', dataIndex: 'label', key: 'label', width: 104 },
  {
    title: '策略',
    key: 'v',
    align: 'right',
    render: (_, r) => <span style={{ color: colorOf(r.val, r.kind), fontWeight: 600 }}>{r.fmt(r.val)}</span>,
  },
  {
    title: '基准',
    key: 'b',
    align: 'right',
    render: (_, r) => <span style={{ color: colorOf(r.base, r.kind) }}>{r.fmt(r.base)}</span>,
  },
  {
    title: '策略 − 基准',
    key: 'd',
    align: 'right',
    render: (_, r) => {
      // 库里只对累计/年化存了 excess_*，其余窗口与比率的差值前端相减
      if (r.val == null || r.base == null) return <span style={{ color: MUTED }}>—</span>
      const d = r.val - r.base
      return <span style={{ color: colorOf(d, r.kind) }}>{r.fmt(d)}</span>
    },
  },
]

function Half({ title, fields, metrics }: { title: string; fields: Field[]; metrics: Partial<Metric> }) {
  const data: MetricRow[] = fields.map((f) => ({ ...f, val: f.v(metrics), base: f.b(metrics) }))
  return (
    <Card title={title} size="small">
      <Table<MetricRow> rowKey="label" size="small" pagination={false} columns={COLS} dataSource={data} />
    </Card>
  )
}

export function MetricsCard({ metrics }: { metrics: Partial<Metric> }) {
  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={12}>
        <Half title="区间收益" fields={LEFT} metrics={metrics} />
      </Col>
      <Col xs={24} lg={12}>
        <Half title="整体与风险" fields={RIGHT} metrics={metrics} />
      </Col>
    </Row>
  )
}
