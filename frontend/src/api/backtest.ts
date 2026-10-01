import { api } from './client'
import type {
  CalendarPoint, CurvePoint, DrawdownPoint, Holding, Metric, ReturnPoint, Strategy, Trade,
} from '../types'

export interface StrategyInput {
  strategy_name?: string
  strategy_method: string
  period?: number | null
  lookback?: number | null
  min_w?: number | null
  max_w?: number | null
  long_window?: number | null
  short_window?: number | null
  codes: string[]
  start_date: string
  end_date?: string                       // 留空 = 跟行情最新交易日
  benchmark?: string
  is_active?: boolean
  note?: string
}

export interface StrategyPatch {
  strategy_name?: string
  note?: string
  is_active?: boolean
}

export async function listStrategies(isActive?: boolean): Promise<Strategy[]> {
  return (await api.get<Strategy[]>('/backtest/strategies', {
    params: isActive == null ? {} : { is_active: isActive },
  })).data
}

export async function createStrategy(body: StrategyInput): Promise<{ strategy_id: number }> {
  return (await api.post('/backtest/strategies', body)).data
}

export async function patchStrategy(id: number, body: StrategyPatch): Promise<Strategy> {
  return (await api.put(`/backtest/strategies/${id}`, body)).data
}

export async function deleteStrategy(id: number): Promise<{ deleted: number }> {
  return (await api.delete(`/backtest/strategies/${id}`)).data
}

export async function runBacktest(): Promise<{ task_id: string }> {
  return (await api.post('/backtest/run')).data
}

export async function getCurve(id: number): Promise<CurvePoint[]> {
  return (await api.get<CurvePoint[]>(`/backtest/strategies/${id}/curve`)).data
}

export async function getDrawdown(id: number): Promise<DrawdownPoint[]> {
  return (await api.get<DrawdownPoint[]>(`/backtest/strategies/${id}/drawdown`)).data
}

export async function getReturns(id: number): Promise<ReturnPoint[]> {
  return (await api.get<ReturnPoint[]>(`/backtest/strategies/${id}/returns`)).data
}

export async function getTrades(id: number): Promise<Trade[]> {
  return (await api.get<Trade[]>(`/backtest/strategies/${id}/trades`)).data
}

export async function getHoldings(id: number): Promise<Holding[]> {
  return (await api.get<Holding[]>(`/backtest/strategies/${id}/holdings`)).data
}

export async function getMetrics(id: number): Promise<Partial<Metric>> {
  return (await api.get(`/backtest/strategies/${id}/metrics`)).data
}

export async function getCalendar(id: number): Promise<CalendarPoint[]> {
  return (await api.get<CalendarPoint[]>(`/backtest/strategies/${id}/calendar`)).data
}