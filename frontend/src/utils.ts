// 前端公用小工具

export const METHOD_CN: Record<string, string> = {
  max_sharpe: '最大夏普',
  min_variance: '最小方差',
  trend: '趋势轮动',
}

const w = (v: any) => `${+(Number(v) * 100).toFixed(1)}%`

// 参数按方法拼成一行中文，如：最大夏普（调仓 1 日 · 回看 20 · 权重 0%~50%）
// 策略（bt_params）和模拟分段（sim_params）字段同名，共用这一份
export function paramText(s: any): string {
  if (!s) return '—'
  const name = METHOD_CN[s.strategy_method] ?? s.strategy_method ?? '—'
  const parts: string[] = []
  if (s.period != null) parts.push(`调仓 ${s.period} 日`)
  if (s.strategy_method === 'trend') {
    if (s.long_window != null) parts.push(`长均线 ${s.long_window}`)
    if (s.short_window != null) parts.push(`短均线 ${s.short_window}`)
  } else {
    if (s.lookback != null) parts.push(`回看 ${s.lookback}`)
    if (s.min_w != null && s.max_w != null) parts.push(`权重 ${w(s.min_w)}~${w(s.max_w)}`)
  }
  return parts.length ? `${name}（${parts.join(' · ')}）` : name
}
