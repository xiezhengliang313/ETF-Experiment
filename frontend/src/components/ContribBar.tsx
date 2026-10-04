import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart } from './EChart'
import type { Contrib } from '../types'

// 与页面其它图一致：正红负绿（A 股习惯）
const RED = '#cf1322'
const GREEN = '#3f8600'
const GRAY = '#8c8c8c'

const money = (v: number | null | undefined) =>
  v == null ? '—' : `¥ ${Math.round(v).toLocaleString('zh-CN')}`
const pct2 = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(2)}%`)
// 价格定长显示：后复权平均成本是算出来的，不格式化会冒出 1.0585938084466548 这种长尾
const num = (v: number | null | undefined, d = 3) => (v == null ? '—' : Number(v).toFixed(d))

// ETF 标的贡献比：横向条形图，x = 收益占比（正红负绿）。
// 悬停给出贡献金额、已实现/浮动、分红、成交笔数与期末持股 —— 把"这个数怎么来的"讲清楚。
export function ContribBar({ contrib, height }: { contrib?: Contrib; height?: number }) {
  const rows = contrib?.rows ?? []

  const option = useMemo<EChartsOption>(() => {
    // y 轴类目自下而上，所以反序 → 贡献最大的排在最上面
    const data = [...(contrib?.rows ?? [])].reverse()
    const labels = data.map((r) => `${r.code}${r.name ? `　${r.name}` : ''}`)
    const shares = data.map((r) => Number(r.share_pct ?? 0))
    const pos = shares.map((v) => (v >= 0 ? v : null))
    const neg = shares.map((v) => (v < 0 ? v : null))

    const tip = (params: any) => {
      const all: any[] = Array.isArray(params) ? params : [params]
      const r = data[all[0]?.dataIndex ?? 0]
      if (!r) return ''
      // 回测按后复权价算、模拟按真实价算，价格口径必须标出来，别两边混着对比
      const basis = contrib?.kind === 'sim' ? '真实价' : '后复权'
      return [
        `<b>${r.code}</b>　${r.name ?? ''}`,
        `收益贡献　${money(r.contrib)}（占比 ${pct2(r.share_pct)}）`,
        `已实现 ${money(r.realized)}　·　浮动 ${money(r.unrealized)}`,
        r.dividend ? `其中现金分红 ${money(r.dividend)}` : '',
        `成交 ${r.trades} 笔　·　手续费 ${money(r.fee)}　·　平均成本 ${num(r.avg_cost, 4)}（${basis}）`,
        r.qty_end
          ? `期末仍持有 ${r.qty_end.toLocaleString('zh-CN')} 份（末价 ${num(r.last_close, 3)}）`
          : '期末已清仓（浮动为 0）',
      ].filter(Boolean).join('<br/>')
    }

    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: tip },
      grid: { left: 8, right: 76, top: 8, bottom: 8, containLabel: true },
      xAxis: {
        type: 'value',
        axisLabel: { formatter: (v: number) => `${v.toFixed(0)}%` },
        splitLine: { lineStyle: { color: '#f0f0f0' } },
      },
      yAxis: { type: 'category', data: labels, axisLabel: { fontSize: 11 }, axisTick: { show: false } },
      series: [
        {
          name: '正贡献',
          type: 'bar',
          barGap: '-100%',
          itemStyle: { color: RED, opacity: 0.75 },
          label: {
            show: true, position: 'right', fontSize: 11,
            formatter: (p: any) => (p.value == null ? '' : `+${Number(p.value).toFixed(2)}%`),
          },
          data: pos,
        },
        {
          name: '负贡献',
          type: 'bar',
          barGap: '-100%',
          itemStyle: { color: GREEN, opacity: 0.75 },
          label: {
            show: true, position: 'left', fontSize: 11,
            formatter: (p: any) => (p.value == null ? '' : `${Number(p.value).toFixed(2)}%`),
          },
          data: neg,
        },
      ],
    }
  }, [contrib])

  if (!rows.length) {
    return (
      <div style={{ fontSize: 12, color: GRAY }}>
        暂无成交数据：跑一次{contrib?.kind === 'sim' ? '模拟' : '回测'}后，这里会按 ETF 标的拆出各自的收益贡献。
      </div>
    )
  }

  return (
    <div>
      <div style={{ fontSize: 12, color: GRAY, marginBottom: 4 }}>
        合计贡献 {money(contrib?.total_pnl)}　·　对账：净值增益 {money(contrib?.nav_gain)}（两者应一致）　·　
        手续费合计 {money(contrib?.total_fee)}　|　贡献 = 卖出 − 买入 − 手续费 + 期末持股市值
        {contrib?.kind === 'sim' ? ' + 持有期分红' : '（后复权口径，分红已内含在价格里）'}
      </div>
      <EChart option={option} height={height ?? Math.max(220, rows.length * 30 + 40)} />
    </div>
  )
}
