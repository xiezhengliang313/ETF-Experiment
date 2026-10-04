import { api } from './client'
import type { CodeItem, QuoteSnapshot } from '../types'

export async function getCodes(): Promise<CodeItem[]> {
  return (await api.get<CodeItem[]>('/market/codes')).data
}

export async function syncMarket(): Promise<{ task_id: string }> {
  return (await api.post('/market/sync')).data
}

export async function generateDividends(): Promise<{ task_id: string }> {
  return (await api.post('/dividends/generate')).data
}

// ETF 实时行情快照：每个标的取库里最新那条（code 传了只看一只）
export async function getQuote(code?: string): Promise<QuoteSnapshot> {
  return (await api.get<QuoteSnapshot>('/quote', { params: code ? { code } : {} })).data
}

// 抓一次实时行情（后台任务；codes 逗号分隔可只抓几只，不传 = etf_bars_raw 全部）
export async function runQuote(codes?: string): Promise<{ task_id: string }> {
  return (await api.post('/quote/run', null, { params: codes ? { codes } : {} })).data
}