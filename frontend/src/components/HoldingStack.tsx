import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart } from './EChart'
import type { Holding } from '../types'

// 悬浮框：只列当天实际持有的成分；权重为 0（含四舍五入后显示 0.00%）的不显示
function tooltipFmt(params: any): string {
  const all: any[] = Array.isArray(params) ? params : [params]
  const rows = all
    .map((p) => ({
      marker: p.marker as string,
      name: p.seriesName as string,
      txt: `${(Number(p.value) * 100).toFixed(2)}%`,
    }))
    .filter((r) => r.txt !== '0.00%')
  if (!rows.length) return ''
  const head: string = all[0]?.axisValueLabel ?? all[0]?.name ?? ''
  return [head, ...rows.map((r) => `${r.marker}${r.name}　${r.txt}`)].join('<br/>')
}

// 历史持仓权重堆积图：x=调仓日，各成分权重堆叠，图例/提示显示 ETF 名称
export function HoldingStack({ holdings, height }: { holdings: Holding[]; height?: number }) {
  const option = useMemo<EChartsOption>(() => {
    const dates = [...new Set(holdings.map((h) => h.rebal_date))].sort()
    const codes = [...new Set(holdings.map((h) => h.code))]
    // 图例/提示用 ETF 名称，缺名称时回退到代码
    const label = new Map(holdings.map((h) => [h.code, h.name || h.code]))
    const series = codes.map((code) => ({
      name: label.get(code) ?? code,
      type: 'line' as const,
      stack: 'total',
      // 只用颜色区分色块，不描边、不画点
      showSymbol: false,
      lineStyle: { width: 0 },
      areaStyle: {},
      emphasis: { focus: 'series' as const },
      data: dates.map((d) => {
        const row = holdings.find((x) => x.rebal_date === d && x.code === code)
        return row ? row.weight : 0
      }),
    }))
    return {
      // 悬浮框只列实际持仓，权重保留 2 位小数并带 %
      tooltip: { trigger: 'axis', formatter: tooltipFmt },
      legend: { type: 'scroll' as const, bottom: 8 },
      grid: { left: 52, right: 16, top: 36, bottom: 56 },
      xAxis: { type: 'category', data: dates },
      yAxis: {
        type: 'value',
        max: 1,
        axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      },
      series,
    }
  }, [holdings])

  return <EChart option={option} height={height ?? 320} />
}