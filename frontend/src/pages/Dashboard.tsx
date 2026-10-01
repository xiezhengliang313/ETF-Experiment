import { useState } from 'react'
import { Button, Card, Table } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import type { TableColumnsType } from 'antd'
import { TaskNotice } from '../components/TaskNotice'
import { useTask } from '../hooks/useTask'
import { listStrategies, runBacktest } from '../api/backtest'
import { listAccounts, runSim } from '../api/simulate'
import type { SimAccount, Strategy } from '../types'

const pct = (v: any) => (v == null ? '—' : `${(v * 100).toFixed(2)}%`)
const num2 = (v: any) => (v == null ? '—' : Number(v).toFixed(2))
const txt = (v: any) => (v == null || v === '' ? '—' : v)

// 收益：正红负绿（A 股习惯）
const ret = (v: any) => {
  if (v == null) return '—'
  const color = v > 0 ? '#cf1322' : v < 0 ? '#3f8600' : undefined
  return <span style={{ color }}>{(v * 100).toFixed(2)}%</span>
}

export default function Dashboard() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  // 进度只体现在按下的那个按钮上（loading）；同时只允许起一个任务，其余按钮先禁用
  const [task, setTask] = useState<{ id: string; key: string } | null>(null)
  const { data: taskData } = useTask(task?.id ?? null)
  const running = taskData?.status === 'queued' || taskData?.status === 'running'
  const busy = (key: string) => ({
    loading: running && task?.key === key,
    disabled: running && task?.key !== key,
  })

  const bt = useQuery({ queryKey: ['strategies', true], queryFn: () => listStrategies(true) })
  const sim = useQuery({ queryKey: ['accounts'], queryFn: listAccounts })

  const refresh = () => qc.invalidateQueries()
  const startTask = (key: string, fn: () => Promise<{ task_id: string }>) => {
    fn().then((r) => setTask({ id: r.task_id, key }))
  }

  // 按年化降序 = 排行榜
  const rows = [...(bt.data ?? [])].sort((a, b) => (b.ret_annual ?? -9) - (a.ret_annual ?? -9))

  // 名称/账户列：够放下最长的「风格轮动-最大夏普」(9 汉字≈126px)+内边距；再长的走 ellipsis。
  // 两张表必须同宽，否则上下两块的指标列对不齐。
  const nameWidth = 180

  const perfCols: TableColumnsType<Strategy> = [
    { title: '名称', dataIndex: 'strategy_name', key: 'name', render: txt, fixed: 'left', width: nameWidth, ellipsis: true, align: 'center' },
    { title: '当日', dataIndex: 'ret_1d', key: 'r1d', render: ret },
    { title: '近一周', dataIndex: 'ret_1w', key: 'r1w', render: ret },
    { title: '近一月', dataIndex: 'ret_1m', key: 'r1m', render: ret },
    { title: '近三月', dataIndex: 'ret_3m', key: 'r3m', render: ret },
    { title: '近一年', dataIndex: 'ret_1y', key: 'r1y', render: ret },
    { title: '近两年', dataIndex: 'ret_2y', key: 'r2y', render: ret },
    { title: '近三年', dataIndex: 'ret_3y', key: 'r3y', render: ret },
    { title: '全期', dataIndex: 'ret_total', key: 'rt', render: ret },
    { title: '年化', dataIndex: 'ret_annual', key: 'ra', render: ret },
    { title: '回撤', dataIndex: 'max_drawdown', key: 'mdd', render: pct },
    { title: '夏普', dataIndex: 'sharpe', key: 'sh', render: num2 },
    { title: '波动率', dataIndex: 'annual_vol', key: 'av', render: pct },
  ]

  const accountCols: TableColumnsType<SimAccount> = [
    { title: '账户', dataIndex: 'account_name', key: 'name', render: txt, fixed: 'left', width: nameWidth, ellipsis: true, align: 'center' },
    { title: '当日', dataIndex: 'ret_1d', key: 'r1d', render: ret },
    { title: '近一周', dataIndex: 'ret_1w', key: 'r1w', render: ret },
    { title: '近一月', dataIndex: 'ret_1m', key: 'r1m', render: ret },
    { title: '近三月', dataIndex: 'ret_3m', key: 'r3m', render: ret },
    { title: '近一年', dataIndex: 'ret_1y', key: 'r1y', render: ret },
    { title: '近两年', dataIndex: 'ret_2y', key: 'r2y', render: ret },
    { title: '近三年', dataIndex: 'ret_3y', key: 'r3y', render: ret },
    { title: '全期', dataIndex: 'ret_total', key: 'rt', render: ret },
    { title: '年化', dataIndex: 'ret_annual', key: 'ra', render: ret },
    { title: '回撤', dataIndex: 'max_drawdown', key: 'mdd', render: pct },
    { title: '夏普', dataIndex: 'sharpe', key: 'sh', render: num2 },
    { title: '波动率', dataIndex: 'annual_vol', key: 'av', render: pct },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card
        title="策略一览"
        size="small"
        extra={<Button type="primary" {...busy('bt')} onClick={() => startTask('bt', runBacktest)}>回测刷新</Button>}
      >
        <Table<Strategy>
          rowKey="strategy_id"
          columns={perfCols}
          dataSource={rows}
          loading={bt.isLoading}
          onRow={(r) => ({
            onClick: () => navigate(`/detail/backtest/${r.strategy_id}`),
            style: { cursor: 'pointer' },
          })}
          size="small"
          scroll={{ x: 'max-content' }}
          pagination={false}
        />
      </Card>

      {task ? <TaskNotice taskId={task.id} onDone={refresh} /> : null}

      <Card
        title="模拟账户"
        size="small"
        extra={<Button type="primary" {...busy('sim')} onClick={() => startTask('sim', runSim)}>模拟刷新</Button>}
      >
        <Table<SimAccount>
          rowKey="account_id"
          columns={accountCols}
          dataSource={sim.data ?? []}
          loading={sim.isLoading}
          onRow={(r) => ({
            onClick: () => navigate(`/detail/sim/${r.account_id}`),
            style: { cursor: 'pointer' },
          })}
          size="small"
          scroll={{ x: 'max-content' }}
          pagination={false}
        />
      </Card>
    </div>
  )
}
