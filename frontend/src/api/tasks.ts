import { api } from './client'
import type { Task } from '../types'

export async function getTask(id: string): Promise<Task> {
  return (await api.get<Task>(`/tasks/${id}`)).data
}