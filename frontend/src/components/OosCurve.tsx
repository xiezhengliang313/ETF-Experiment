import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart } from './EChart'
import { paramText } from '../utils'
import type { OosPoint, OptFold } from '../types'

// 样本外拼接净值曲线：各折样本外段首尾相接拼成的一条线（起点 1.0）。
// 折分界画竖虚线，悬停显示当天属于第几折、该折选出的参数、该折样本外成绩 ——
// 这样参数在折间跳变时，能直接看出「换了参数之后走势怎么变」，也就是参数稳不稳。
export function OosCurve({ points, folds, method, height }: {
  points: OosPoint[]
  folds: OptFold[]
  method: string
  height?: number
}) {
  const option = useMemo<EChartsOption>(() => {
    const dates = points.map((p) => p.trade_date)
    const byFold = new Map(folds.map((f) => [f.fold, f]))
    const label = (f?: OptFold) => (f ? paramText({ ...f.params, strategy_method: method }) : '—')
    const pct = (v?: number | null) => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(2)}%`)

    // 折分界取「该折在曲线上的第一个交易日」：步长小于样本外窗长时会裁掉重叠段，
    // 用折窗口里的 test_start 反而可能不在曲线上，所以从数据本身推。
    const bounds = points
      .map((p, i) => (i > 0 && p.fold !== points[i - 1].fold ? { date: p.trade_date, fold: p.fold } : null))
      .filter((b): b is { date: string; fold: number } => b !== null)

    const tooltipFmt = (params: any): string => {
      const p = Array.isArray(params) ? params[0] : params
      const cur: OosPoint | undefined = points[p?.dataIndex as number]
      if (!cur) return ''
      const f = byFold.get(cur.fold)
      const lines = [
        `${cur.trade_date}　净值 ${Number(cur.nav).toFixed(4)}`,
        `第 ${cur.fold}/${folds.length} 折　参数：${label(f)}`,
      ]
      if (f) lines.push(`该折样本外年化 ${pct(f.oos_ret_annual)}　回撤 ${pct(f.oos_max_drawdown)}`)
      return lines.join('<br/>')
    }

    return {
      tooltip: { trigger: 'axis', formatter: tooltipFmt },
      grid: { left: 56, right: 20, top: 28, bottom: 44 },
      xAxis: { type: 'category', data: dates, boundaryGap: false },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { formatter: (v: number) => v.toFixed(2) },
      },
      // 曲线可能有上千个交易日（20 折 × 126 天），允许框选放大看某几折
      dataZoom: [{ type: 'inside' }],
      series: [
        {
          name: '样本外拼接净值',
          type: 'line',
          showSymbol: false,
          data: points.map((p) => p.nav),
          lineStyle: { width: 1.6 },
          areaStyle: { opacity: 0.08 },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { type: 'dashed', color: '#bfbfbf' },
            label: { formatter: '{b}', position: 'insideEndTop', color: '#8c8c8c', fontSize: 11 },
            data: [
              // 起点（净值 1.0）参考线，便于看整条曲线是盈是亏
              { yAxis: 1, lineStyle: { type: 'solid', color: '#d9d9d9' }, label: { formatter: '1.00' } },
              ...bounds.map((b) => ({ xAxis: b.date, name: `折${b.fold}` })),
            ],
          },
        },
      ],
    }
  }, [points, folds, method])

  if (!points.length) return <div style={{ color: '#8c8c8c' }}>暂无样本外曲线数据</div>
  return <EChart option={option} height={height ?? 300} />
}
