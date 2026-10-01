import { useMemo } from 'react'
import type { EChartsOption } from 'echarts'
import { EChart } from './EChart'
import type { ReturnPoint } from '../types'

// 策略/账户线红、基准线灰（与业绩对比、回撤对比保持一致）
const RED = '#cf1322'
const GRAY = '#8c8c8c'
const AXIS = '#54555a'
// 直方图分箱数：30 箱在 8 年日线（约 1900 天）上也够看，不会碎成锯齿
const BINS = 30

// 正态概率密度
const pdf = (x: number, mu: number, sd: number) =>
  Math.exp(-((x - mu) ** 2) / (2 * sd * sd)) / (sd * Math.sqrt(2 * Math.PI))

const mean = (a: number[]) => a.reduce((s, x) => s + x, 0) / a.length
const std = (a: number[], mu: number) =>
  Math.sqrt(a.reduce((s, x) => s + (x - mu) ** 2, 0) / Math.max(1, a.length - 1))
// 超额峰度：0 = 正态，>0 = 尖峰厚尾（极值比正态多）
const kurt = (a: number[], mu: number, sd: number) =>
  a.reduce((s, x) => s + ((x - mu) / sd) ** 4, 0) / a.length - 3
// 分位数（线性插值），用来裁掉极值撑出来的空轴
const quantile = (sorted: number[], q: number) => {
  if (!sorted.length) return 0
  const p = (sorted.length - 1) * q
  const lo = Math.floor(p)
  const hi = Math.ceil(p)
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (p - lo)
}

const pct2 = (v: number) => `${(v * 100).toFixed(2)}%`

// 日收益分布：直方图（策略/账户 + 基准）+ 各自的正态拟合曲线，看实际分布比正态胖多少
export function ReturnHistogram({ points, label, height }: {
  points: ReturnPoint[]
  label?: string
  height?: number
}) {
  const name = label ?? '策略'

  const { option, stat } = useMemo(() => {
    const acc = points.map((p) => p.ret).filter((v): v is number => v != null)
    const bm = points.map((p) => p.benchmark_ret).filter((v): v is number => v != null)
    if (!acc.length) return { option: {} as EChartsOption, stat: null }

    // 极端的一两天（可达 ±15%）会把 x 轴撑得极长、中间 99% 的箱挤成一根针，
    // 所以按 1%/99% 分位裁掉尾巴：超出范围的日子并进两端那两个箱，一天不丢（悬浮框会说明）。
    const sorted = [...acc, ...bm].sort((a, b) => a - b)
    const lo = quantile(sorted, 0.01)
    const hi = quantile(sorted, 0.99)
    const step = (hi - lo) / BINS || 1
    const binOf = (v: number) => Math.min(BINS - 1, Math.max(0, Math.floor((v - lo) / step)))
    const belowAcc = acc.filter((v) => v < lo).length
    const aboveAcc = acc.filter((v) => v > hi).length
    const belowBm = bm.filter((v) => v < lo).length
    const aboveBm = bm.filter((v) => v > hi).length
    const counts = (arr: number[]) => {
      const c = new Array(BINS).fill(0) as number[]
      arr.forEach((v) => { c[binOf(v)] += 1 })
      return c
    }
    const accC = counts(acc)
    const bmC = counts(bm)
    // 箱中心当作正态曲线的 x
    const centers = Array.from({ length: BINS }, (_, i) => lo + step * (i + 0.5))
    const mA = mean(acc)
    const sA = std(acc, mA)
    const mB = bm.length ? mean(bm) : 0
    const sB = bm.length ? std(bm, mB) : 1
    // 正态曲线要乘「天数 × 箱宽」，才和直方图的次数同一量纲，能叠在一张图上
    const fit = (n: number, mu: number, sd: number) =>
      centers.map((x) => (sd > 0 && n ? n * step * pdf(x, mu, sd) : 0))
    const accFit = fit(acc.length, mA, sA)   // 只画一条正态曲线（按策略/账户拟合），不区分基准

    // 两端箱的名字写成「< x%」「≥ y%」，因为它们还兜着被裁掉的极端值
    const axisLabels = centers.map((x, i) =>
      i === 0 ? `< ${pct2(lo + step)}` : i === BINS - 1 ? `≥ ${pct2(hi - step)}` : pct2(x))
    const share = (n: number, tot: number) => (tot ? ((n / tot) * 100).toFixed(1) : '0.0')
    const tip = (params: any) => {
      const all: any[] = Array.isArray(params) ? params : [params]
      const i = all[0]?.dataIndex ?? 0
      const head =
        i === 0
          ? `< ${pct2(lo + step)}（含更低的 ${belowAcc} 天 / 基准 ${belowBm} 天）`
          : i === BINS - 1
            ? `≥ ${pct2(hi - step)}（含更高的 ${aboveAcc} 天 / 基准 ${aboveBm} 天）`
            : `${pct2(lo + step * i)} ~ ${pct2(lo + step * (i + 1))}`
      return [
        head,
        `${name}　${accC[i]} 天（${share(accC[i], acc.length)}%）`,
        `基准　${bmC[i]} 天（${share(bmC[i], bm.length)}%）`,
        `正态分布　${accFit[i].toFixed(1)} 天`,
      ].join('<br/>')
    }

    const opt: EChartsOption = {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: tip },
      legend: { data: [`${name}日收益`, '基准日收益', '正态分布'], bottom: 8 },
      grid: { left: 52, right: 16, top: 36, bottom: 56 },
      xAxis: {
        type: 'category',
        data: axisLabels,
        // 每 5 个标签显示一个，但两端的「< x%」「≥ y%」一定要显示出来
        axisLabel: { interval: (i: number) => i % 5 === 0 || i === BINS - 1, fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        name: '天数',
        nameTextStyle: { fontSize: 11 },
        axisLine: { show: true, lineStyle: { color: AXIS } },
        splitLine: { show: true, lineStyle: { color: '#f0f0f0' } },
      },
      series: [
        { name: `${name}日收益`, type: 'bar', data: accC, itemStyle: { color: RED, opacity: 0.65 } },
        { name: '基准日收益', type: 'bar', data: bmC, itemStyle: { color: GRAY, opacity: 0.45 } },
        {
          name: '正态分布', type: 'line', smooth: true, showSymbol: false, z: 5,
          lineStyle: { color: RED, width: 2 }, data: accFit,
        },
      ],
    }

    const statText = [
      `${name} ${acc.length} 天：均值 ${pct2(mA)} · 标准差 ${pct2(sA)} · 峰度 ${kurt(acc, mA, sA).toFixed(2)}`,
      bm.length
        ? `基准 ${bm.length} 天：均值 ${pct2(mB)} · 标准差 ${pct2(sB)} · 峰度 ${kurt(bm, mB, sB).toFixed(2)}`
        : '基准：无数据',
    ].join('　|　')

    return { option: opt, stat: statText }
  }, [points, name])

  return (
    <div>
      {stat ? (
        <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>
          {stat}
          <span style={{ marginLeft: 8 }}>
            （x 轴按 1%~99% 分位裁剪、极端值并入两端箱；正态曲线按{name}的均值、标准差拟合；峰度 0 = 正态，越大越尖峰厚尾）
          </span>
        </div>
      ) : null}
      <EChart option={option} height={height ?? 320} />
    </div>
  )
}
