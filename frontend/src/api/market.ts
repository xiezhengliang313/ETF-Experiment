import { api } from './client'
import type { CodeItem } from '../types'

export async function getCodes(): Promise<CodeItem[]> {
  return (await api.get<CodeItem[]>('/market/codes')).data
}

export async function syncMarket(): Promise<{ task_id: string }> {
  return (await api.post('/market/sync')).data
}

export async function generateDividends(): Promise<{ task_id: string }> {
  return (await api.post('/dividends/generate')).data
}