import { api } from './client'
import type { OosPoint, OptFold, OptWinner } from '../types'

// 寻优胜出参数，一次寻优一行（滚动寻优批次在前，旧方法的单段批次在后）
export async function getWinners(): Promise<OptWinner[]> {
  return (await api.get<OptWinner[]>('/optimize/winners')).data
}

// 滚动寻优的每折明细：训练窗 vs 样本外窗
export async function getWalkFolds(optId: number): Promise<OptFold[]> {
  return (await api.get<OptFold[]>(`/optimize/walk/${optId}/folds`)).data
}

// 样本外拼接净值曲线：各折样本外段首尾相接拼成的一条线（与 oos_* 指标同源）
export async function getWalkOos(optId: number): Promise<OosPoint[]> {
  return (await api.get<OosPoint[]>(`/optimize/walk/${optId}/oos`)).data
}

export async function runOptimize(body?: { workers?: number }): Promise<{ task_id: string }> {
  return (await api.post('/optimize/run', body ?? {})).data
}
