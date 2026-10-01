import { useQuery } from '@tanstack/react-query'
import { getTask } from '../api/tasks'

// 轮询任务状态：未结束时每 3 秒查一次，done / error 停
export function useTask(taskId: string | null) {
  return useQuery({
    queryKey: ['task', taskId],
    queryFn: () => getTask(taskId!),
    enabled: !!taskId,
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === 'done' || s === 'error' ? false : 3000
    },
  })
}