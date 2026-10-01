import { api } from './client'
import type {
  CalendarPoint, CurvePoint, DrawdownPoint, Holding, ReturnPoint, SimAccount, SimSegment, Trade,
} from '../types'

export interface AccountInput {
  account_name?: string
  init_cash?: number
  start_date: string
  end_date?: string | null
  benchmark?: string
  is_active?: boolean
  note?: string
}

export interface SegmentInput {
  strategy_name?: string
  strategy_method?: string
  period?: number | null
  lookback?: number | null
  min_w?: number | null
  max_w?: number | null
  long_window?: number | null
  short_window?: number | null
  codes?: string[]
  start_date?: string
  end_date?: string
  copy_from_strategy_id?: number
}

export interface SegmentPatchInput {
  strategy_name?: string
  end_date?: string
  note?: string
}

// 改 end_date 时后端会把该账户「从哪天起」的结果作废，并回报作废了多少行
export type SegmentPatchResult = SimSegment & { invalidated_rows?: number; recompute_from?: string }

export async function listAccounts(): Promise<SimAccount[]> {
  return (await api.get<SimAccount[]>('/sim/accounts')).data
}

export async function createAccount(body: AccountInput): Promise<{ account_id: number }> {
  return (await api.post('/sim/accounts', body)).data
}

export async function deleteAccount(id: number): Promise<{ deleted: number }> {
  return (await api.delete(`/sim/accounts/${id}`)).data
}

export async function listSegments(accountId: number): Promise<SimSegment[]> {
  return (await api.get<SimSegment[]>(`/sim/accounts/${accountId}/segments`)).data
}

export async function createSegment(accountId: number, body: SegmentInput): Promise<{ strategy_id: number }> {
  return (await api.post(`/sim/accounts/${accountId}/segments`, body)).data
}

export async function patchSegment(accountId: number, sid: number,
                                   body: SegmentPatchInput): Promise<SegmentPatchResult> {
  return (await api.put(`/sim/accounts/${accountId}/segments/${sid}`, body)).data
}

export async function deleteSegment(accountId: number, sid: number): Promise<{ deleted: number }> {
  return (await api.delete(`/sim/accounts/${accountId}/segments/${sid}`)).data
}

export async function runSim(): Promise<{ task_id: string }> {
  return (await api.post('/sim/run')).data
}

export async function getAccountCurve(id: number): Promise<CurvePoint[]> {
  return (await api.get<CurvePoint[]>(`/sim/accounts/${id}/curve`)).data
}

export async function getAccountDrawdown(id: number): Promise<DrawdownPoint[]> {
  return (await api.get<DrawdownPoint[]>(`/sim/accounts/${id}/drawdown`)).data
}

export async function getAccountReturns(id: number): Promise<ReturnPoint[]> {
  return (await api.get<ReturnPoint[]>(`/sim/accounts/${id}/returns`)).data
}

export async function getAccountTrades(id: number): Promise<Trade[]> {
  return (await api.get<Trade[]>(`/sim/accounts/${id}/trades`)).data
}

export async function getAccountHoldings(id: number): Promise<Holding[]> {
  return (await api.get<Holding[]>(`/sim/accounts/${id}/holdings`)).data
}

export async function getAccountCalendar(id: number): Promise<CalendarPoint[]> {
  return (await api.get<CalendarPoint[]>(`/sim/accounts/${id}/calendar`)).data
}