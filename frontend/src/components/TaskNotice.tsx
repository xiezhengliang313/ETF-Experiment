import { useEffect, useRef } from 'react'
import { Alert } from 'antd'
import { useTask } from '../hooks/useTask'

// 只有「结束」时才需要给个交代：跑的过程中进度体现在按下的那个按钮上（loading），
// 所以这里运行中不渲染任何东西 —— 不再把任务日志（[BT] …）摊在页面上。
function tail(log: string, n = 800): string {
  return log.length > n ? log.slice(-n) : log
}

export function TaskNotice({ taskId, onDone }: { taskId: string; onDone?: () => void }) {
  const { data } = useTask(taskId)
  const doneRef = useRef(false)

  useEffect(() => {
    if (data?.status === 'done' && !doneRef.current) {
      doneRef.current = true
      onDone?.()
    }
  }, [data?.status])

  if (data?.status === 'done') {
    return <Alert type="success" showIcon closable message={`${data.name} 完成`} />
  }
  if (data?.status === 'error') {
    // 失败例外：错误原因必须能看见
    return (
      <Alert
        type="error"
        showIcon
        closable
        message={`${data.name} 失败`}
        description={<pre className="pre-log">{tail(data.log || '未知错误')}</pre>}
      />
    )
  }
  return null
}
