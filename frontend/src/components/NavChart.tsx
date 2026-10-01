import type { EChartsOption } from 'echarts'
import { EChart } from './EChart'
import type { CurvePoint, DrawdownPoint } from '../types'

// 策略线红、基准线灰（series 按顺序取色）
const RED = '#cf1322'
const GRAY = '#8c8c8c'
// ECharts 默认轴线色（tokens.color.axisLine = neutral70），y 轴显式设置以对齐 x 轴
const AXIS = '#54555a'
// 回撤图面积填充：半透明，两条曲线叠加时都能看清
const RED_FILL = 'rgba(207, 19, 34, 0.18)'
const GRAY_FILL = 'rgba(140, 140, 140, 0.18)'
// 悬浮框数值统一保留 2 位小数
const NUM2 = (v: unknown) => Number(v).toFixed(2)
// 回撤是百分比，悬浮框按 % 显示（与 y 轴刻度一致）
const PCT2 = (v: unknown) => `${(Number(v) * 100).toFixed(2)}%`

export function NavChart({ kind, points, height }: {
  kind: 'nav' | 'drawdown'
  points: CurvePoint[] | DrawdownPoint[]
  height?: number
}) {
  const dates = points.map((p) => p.trade_date)

  const option: EChartsOption =
    kind === 'nav'
      ? {
          color: [RED, GRAY],
          tooltip: { trigger: 'axis', valueFormatter: NUM2 },
          legend: { data: ['策略', '基准'], bottom: 8 },
          grid: { left: 52, right: 16, top: 36, bottom: 56 },
          xAxis: { type: 'category', data: dates },
          yAxis: {
            type: 'value',
            scale: true,
            axisLine: { show: true, lineStyle: { color: AXIS } },
            splitLine: { show: true, lineStyle: { color: '#f0f0f0' } },
          },
          series: [
            { name: '策略', type: 'line', showSymbol: false, data: points.map((p) => (p as CurvePoint).nav) },
            { name: '基准', type: 'line', showSymbol: false, data: points.map((p) => (p as CurvePoint).benchmark_nav) },
          ],
        }
      : {
          color: [RED, GRAY],
          tooltip: { trigger: 'axis', valueFormatter: PCT2 },
          legend: { data: ['策略回撤', '基准回撤'], bottom: 8 },
          grid: { left: 52, right: 16, top: 36, bottom: 56 },
          xAxis: { type: 'category', data: dates },
          yAxis: {
            type: 'value',
            axisLine: { show: true, lineStyle: { color: AXIS } },
            splitLine: { show: true, lineStyle: { color: '#f0f0f0' } },
            axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
          },
          series: [
            {
              name: '策略回撤',
              type: 'line',
              showSymbol: false,
              z: 3,
              lineStyle: { color: RED },
              areaStyle: { color: RED_FILL },
              data: points.map((p) => (p as DrawdownPoint).strategy_dd),
            },
            {
              name: '基准回撤',
              type: 'line',
              showSymbol: false,
              z: 2,
              lineStyle: { color: GRAY },
              areaStyle: { color: GRAY_FILL },
              data: points.map((p) => (p as DrawdownPoint).benchmark_dd),
            },
          ],
        }

  return <EChart option={option} height={height ?? 320} />
}
