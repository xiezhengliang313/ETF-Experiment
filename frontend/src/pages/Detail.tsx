import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { Alert, Card, Col, Descriptions, Row, Table, Tabs, Tag } from 'antd'
import type { TableColumnsType } from 'antd'
import { MetricsCard } from '../components/MetricsCard'
import { NavChart } from '../components/NavChart'
import { HoldingStack } from '../components/HoldingStack'
import { ReturnHistogram } from '../components/ReturnHistogram'
import {
  getCalendar, getCurve, getDrawdown, getHoldings, getMetrics, getReturns, getTrades, listStrategies,
} from '../api/backtest'
import {
  getAccountCalendar, getAccountCurve, getAccountDrawdown, getAccountHoldings,
  getAccountReturns, getAccountTrades, listAccounts, listSegments,
} from '../api/simulate'
import type { Holding, Metric, Trade } from '../types'
import { METHOD_CN, paramText } from '../utils'

const RED = '#cf1322'
const GREEN = '#3f8600'
const DARK = '#1f1f1f'
const GRAY = '#8c8c8c'

const pct = (v: any) => (v == null ? '—' : `${(v * 100).toFixed(2)}%`)
const pctSigned = (v: any) => (v == null ? '—' : `${v > 0 ? '+' : ''}${(v * 100).toFixed(2)}%`)
// 库里只存了全期/年化的 excess_*；其余窗口的"超额"由前端相减
const diffPct = (a: any, b: any) => (a == null || b == null ? '—' : pctSigned(Number(a) - Number(b)))
const num2 = (v: any) => (v == null ? '—' : Number(v).toFixed(2))
const money = (v: any) => (v == null ? '—' : `¥ ${Number(v).toLocaleString('zh-CN')}`)
// 接口给的是 ISO（带时区），截到分钟即可
const fmtTime = (v: any) => (v == null ? '—' : String(v).slice(0, 16).replace('T', ' '))
// 收益类正红负绿（A 股习惯）；风险类固定深色，否则回撤恒为负会一路绿
const retColor = (v: any) => (v == null ? DARK : v > 0 ? RED : v < 0 ? GREEN : DARK)

const SOURCE_CN: Record<string, string> = { optimize: '寻优', manual: '手工' }
const tagStyle: React.CSSProperties = { marginInlineEnd: 0 }

// 页头关键数字
type HeadStat = { label: string; text: string; color: string; sub: string }

export default function Detail({ kind }: { kind: 'backtest' | 'sim' }) {
  const { id } = useParams()
  const nid = Number(id)
  const isBt = kind === 'backtest'

  const metricsB = useQuery({
    queryKey: ['metrics', nid],
    queryFn: () => getMetrics(nid),
    enabled: isBt && !!id,
  })
  const accounts = useQuery({
    queryKey: ['accounts'],
    queryFn: listAccounts,
    enabled: kind === 'sim',
  })
  const strategies = useQuery({
    queryKey: ['strategies', undefined],
    queryFn: () => listStrategies(),
    enabled: isBt,
  })
  const segments = useQuery({
    queryKey: ['segments', nid],
    queryFn: () => listSegments(nid),
    enabled: kind === 'sim' && !!id,
  })
  const curve = useQuery({
    queryKey: ['curve', kind, nid],
    queryFn: () => (isBt ? getCurve(nid) : getAccountCurve(nid)),
    enabled: !!id,
  })
  const dd = useQuery({
    queryKey: ['dd', kind, nid],
    queryFn: () => (isBt ? getDrawdown(nid) : getAccountDrawdown(nid)),
    enabled: !!id,
  })
  // 逐日涨跌幅（后端算好），给日收益分布图用
  const ret = useQuery({
    queryKey: ['returns', kind, nid],
    queryFn: () => (isBt ? getReturns(nid) : getAccountReturns(nid)),
    enabled: !!id,
  })
  const holdings = useQuery({
    queryKey: ['holdings', kind, nid],
    queryFn: () => (isBt ? getHoldings(nid) : getAccountHoldings(nid)),
    enabled: !!id,
  })
  const trades = useQuery({
    queryKey: ['trades', kind, nid],
    queryFn: () => (isBt ? getTrades(nid) : getAccountTrades(nid)),
    enabled: !!id,
  })
  const cal = useQuery({
    queryKey: ['cal', kind, nid],
    queryFn: () => (isBt ? getCalendar(nid) : getAccountCalendar(nid)),
    enabled: !!id,
  })

  const strategy = strategies.data?.find((s) => s.strategy_id === nid)
  const account = accounts.data?.find((a) => a.account_id === nid)
  const metrics: Partial<Metric> | undefined = isBt ? metricsB.data : account

  // ===== 页头：身份 =====
  const kindCn = isBt ? '策略' : '账户'
  const nameText = isBt ? (strategy?.strategy_name ?? `策略 #${id}`) : (account?.account_name ?? `账户 #${id}`)
  const rangeText = isBt
    ? strategy
      ? `${strategy.start_date} ~ ${strategy.end_date ?? '最新'}`
      : '—'
    : account
      ? `${account.start_date} ~ ${account.end_date ?? '最新'}`
      : '—'
  const paramsText = isBt
    ? paramText(strategy)
    : (segments.data ?? []).length
      ? (segments.data ?? []).map((sg) => paramText(sg)).join(' ； ')
      : '—'
  const isActive = isBt ? strategy?.is_active : account?.is_active
  const metricsAt = isBt ? strategy?.metrics_at : account?.metrics_at
  const bmCode = (isBt ? strategy?.benchmark : account?.benchmark) ?? 'sh510300'

  // ===== 页头：6 个关键数字（各带基准对照）=====
  const stats: HeadStat[] = [
    {
      label: '当日',
      text: pct(metrics?.ret_1d),
      color: retColor(metrics?.ret_1d),
      sub: `基准 ${pct(metrics?.bm_ret_1d)} · 超额 ${diffPct(metrics?.ret_1d, metrics?.bm_ret_1d)}`,
    },
    {
      label: '近一周',
      text: pct(metrics?.ret_1w),
      color: retColor(metrics?.ret_1w),
      sub: `基准 ${pct(metrics?.bm_ret_1w)} · 超额 ${diffPct(metrics?.ret_1w, metrics?.bm_ret_1w)}`,
    },
    {
      label: '年化收益',
      text: pct(metrics?.ret_annual),
      color: retColor(metrics?.ret_annual),
      sub: `基准 ${pct(metrics?.bm_ret_annual)} · 超额 ${pctSigned(metrics?.excess_annual)}`,
    },
    { label: '最大回撤', text: pct(metrics?.max_drawdown), color: DARK, sub: `基准 ${pct(metrics?.bm_max_drawdown)}` },
    { label: '夏普', text: num2(metrics?.sharpe), color: DARK, sub: `基准 ${num2(metrics?.bm_sharpe)}` },
    { label: '年化波动率', text: pct(metrics?.annual_vol), color: DARK, sub: `基准 ${pct(metrics?.bm_annual_vol)}` },
  ]

  const descItems = [
    { key: 'range', label: isBt ? '回测区间' : '模拟区间', children: rangeText },
    { key: 'params', label: '参数组合', children: paramsText },
    { key: 'at', label: '指标计算时间', children: fmtTime(metricsAt) },
    ...(isBt ? [] : [{ key: 'cash', label: '初始资金', children: money(account?.init_cash) }]),
  ]

  // ===== 持仓 / 调仓 =====
  const hs = holdings.data ?? []
  // 页头「下一调仓日」提示用（调仓日历整表已挪到"持仓与调仓"里被堆积图替换）。
  // 一个账户可能有多个分段、各自一条未来日历行，所以先收齐，再优先挑"有计划持仓"的那条。
  const futRows = (cal.data ?? []).filter((c) => c.is_future)
  const futDates = new Set(futRows.map((c) => c.rebal_date))
  // 前瞻信号：rebal_date 落在未来日历里的那几行 = 按信号日收盘价算出的「计划持仓」
  const plan = hs.filter((h) => futDates.has(h.rebal_date))
  const future = (plan.length ? futRows.find((c) => c.rebal_date === plan[0].rebal_date) : undefined) ?? futRows[0]
  // 已实现的部分（历史 + 最近一次调仓）：未来那次不算"当前持仓"，也不进堆积图
  const realized = hs.filter((h) => !futDates.has(h.rebal_date))
  // 取最大调仓日（不依赖接口返回顺序）
  const lastRebal = realized.length ? realized.map((h) => h.rebal_date).sort().pop() : undefined
  // 当前持仓只展示实际有仓位的（目标股数 > 0）
  const current = realized.filter((h) => h.rebal_date === lastRebal && (h.target_qty ?? 0) > 0)
  const curQty = new Map(current.map((h) => [h.code, h.target_qty ?? 0]))
  // 计划行：目标有仓位、或当前还持有（这次要清仓）、或权重非零（含优化器给出的极小值）
  const planRows = plan
    .filter((h) => (h.target_qty ?? 0) > 0 || curQty.get(h.code) || Math.abs(Number(h.weight ?? 0)) > 1e-6)
    .map((h) => ({ ...h, delta: (h.target_qty ?? 0) - (curQty.get(h.code) ?? 0) }))
  // 交易明细倒序：最新交易排在最上面（同一日期保持接口原有顺序，sort 是稳定的）
  const tradeRows = [...(trades.data ?? [])].sort((a, b) =>
    a.trade_date < b.trade_date ? 1 : a.trade_date > b.trade_date ? -1 : 0,
  )

  const holdingCols: TableColumnsType<Holding> = [
    { title: '代码', dataIndex: 'code', key: 'code' },
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '权重', dataIndex: 'weight', key: 'w', render: pct },
    { title: '目标股数', dataIndex: 'target_qty', key: 'qty' },
    { title: '信号收盘', dataIndex: 'signal_close', key: 'sc' },
  ]

  // 前瞻信号（下一调仓日的计划持仓）：比"当前持仓"多一列「较当前」，直接看出这次买卖多少股
  const planCols: TableColumnsType<Holding & { delta: number }> = [
    { title: '代码', dataIndex: 'code', key: 'code' },
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '目标权重', dataIndex: 'weight', key: 'w', render: pct },
    { title: '信号收盘', dataIndex: 'signal_close', key: 'sc' },
    { title: '计划股数', dataIndex: 'target_qty', key: 'qty' },
    {
      title: '较当前', dataIndex: 'delta', key: 'd',
      render: (v: number) => (v === 0 ? '—'
        : <span style={{ color: v > 0 ? RED : GREEN }}>{v > 0 ? '买' : '卖'} {Math.abs(v).toLocaleString('zh-CN')}</span>),
    },
  ]

  const tradeCols: TableColumnsType<Trade> = [
    { title: '日期', dataIndex: 'trade_date', key: 'd' },
    { title: '代码', dataIndex: 'code', key: 'code' },
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '方向', dataIndex: 'side', key: 'side' },
    { title: '数量', dataIndex: 'qty', key: 'qty' },
    { title: '价格', dataIndex: 'price', key: 'price' },
    { title: '金额', dataIndex: 'amount', key: 'amount' },
    { title: '费用', dataIndex: 'fee', key: 'fee' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* ===== 页头：名称 + 标签 + 关键数字 + 明细信息 ===== */}
      <Card styles={{ body: { padding: '16px 20px' } }}>
        <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <span style={{ fontSize: 20, fontWeight: 600 }}>{nameText}</span>
          {isBt ? (
            <Tag color="blue" style={tagStyle}>{METHOD_CN[strategy?.strategy_method ?? ''] ?? strategy?.strategy_method ?? '—'}</Tag>
          ) : (
            <Tag color="purple" style={tagStyle}>模拟盘</Tag>
          )}
          {isBt && strategy?.source ? (
            <Tag style={tagStyle}>{SOURCE_CN[strategy.source] ?? strategy.source}</Tag>
          ) : null}
          <Tag color={isActive ? 'green' : 'default'} style={tagStyle}>{isActive ? '有效' : '已停用'}</Tag>
          <Tag color="gold" style={tagStyle}>基准 {bmCode}</Tag>
        </div>

        {/* 6 个数字正好铺满 24 栅格（6 × 4），桌面一行；窄屏自动降为 3 个/2 个一行 */}
        <Row gutter={[16, 12]} style={{ marginTop: 14 }}>
          {stats.map((s) => (
            <Col xs={12} sm={8} md={4} key={s.label}>
              <div style={{ fontSize: 12, color: GRAY }}>{s.label}</div>
              <div style={{ fontSize: 22, fontWeight: 600, lineHeight: '30px', color: s.color, whiteSpace: 'nowrap' }}>
                {s.text}
              </div>
              <div style={{ fontSize: 12, color: GRAY }}>{s.sub}</div>
            </Col>
          ))}
        </Row>

        <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }} style={{ marginTop: 16 }} items={descItems} />

        {future ? (
          <Alert
            style={{ marginTop: 12 }}
            type={planRows.length ? 'success' : 'info'}
            showIcon
            message={`下一调仓日 ${future.rebal_date}（信号日 ${future.signal_date}）${planRows.length ? ' · 计划持仓' : ''}`}
            description={planRows.length ? (
              <div>
                <div style={{ marginBottom: 8 }}>
                  按信号日 {future.signal_date} 收盘价 + 当日实际持股与现金（权益 {money(planRows[0].signal_equity)}）算出；
                  「较当前」= 相对最近一次调仓（{lastRebal}）的计划增减。实际成交价/股数还会受{ future.rebal_date }当天价格、可用现金与手续费影响。
                </div>
                <Table rowKey="code" columns={planCols} dataSource={planRows} pagination={false} size="small" />
              </div>
            ) : (
              `这次还没算出标的：目标持仓要用信号日收盘价算，等信号日（${future.signal_date}）到了之后再重跑一次${isBt ? '回测' : '模拟'}，就会出现在这里。`
            )}
          />
        ) : null}
      </Card>

      {/* ===== 分区：指标 / 持仓与调仓 / 走势图 / 交易明细 ===== */}
      <Tabs
        items={[
          {
            key: 'metrics',
            label: '指标对比',
            children: <MetricsCard metrics={metrics ?? {}} />,
          },
          {
            key: 'pos',
            label: '持仓与调仓',
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <Card title={`当前持仓（最近调仓日 ${lastRebal ?? '—'}）`} size="small">
                  <Table
                    rowKey={(_, i) => String(i)}
                    columns={holdingCols}
                    dataSource={current}
                    pagination={false}
                    size="small"
                  />
                </Card>
                <Card title="历史持仓权重堆积图" size="small">
                  {/* 堆积图只看已实现的调仓（未来那次是计划，不算历史权重） */}
                  <HoldingStack holdings={realized} height={380} />
                </Card>
              </div>
            ),
          },
          {
            key: 'charts',
            label: '走势图',
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <Card title={`业绩对比（${kindCn} vs 基准）`} size="small">
                  <NavChart kind="nav" points={curve.data ?? []} />
                </Card>
                <Card title="最大回撤对比" size="small">
                  <NavChart kind="drawdown" points={dd.data ?? []} />
                </Card>
                <Card title="日收益分布（对比正态分布）" size="small">
                  <ReturnHistogram points={ret.data ?? []} label={kindCn} />
                </Card>
              </div>
            ),
          },
          {
            key: 'trades',
            label: `交易明细（${tradeRows.length}）`,
            children: (
              <Card size="small">
                <Table
                  rowKey={(_, i) => String(i)}
                  columns={tradeCols}
                  dataSource={tradeRows}
                  pagination={{ pageSize: 10, showSizeChanger: false }}
                  size="small"
                />
              </Card>
            ),
          },
        ]}
      />
    </div>
  )
}
