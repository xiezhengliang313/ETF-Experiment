import { useState } from 'react'
import {
  Alert, Button, Card, ConfigProvider, DatePicker, Form, Input, InputNumber, Modal, Popconfirm, Space, Switch, Table, Tabs,
} from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs, { type Dayjs } from 'dayjs'
import type { TableColumnsType } from 'antd'
import { OosCurve } from '../components/OosCurve'
import { ParamsForm, type ParamsValues } from '../components/ParamsForm'
import { TaskNotice } from '../components/TaskNotice'
import { useTask } from '../hooks/useTask'
import { generateDividends, getCodes, syncMarket } from '../api/market'
import { createStrategy, deleteStrategy, listStrategies, patchStrategy } from '../api/backtest'
import { getWalkFolds, getWalkOos, getWinners, runOptimize } from '../api/optimize'
import {
  createAccount, createSegment, deleteAccount, deleteSegment, listAccounts, listSegments, patchSegment,
} from '../api/simulate'
import type { OptWinner, SimAccount, SimSegment, Strategy } from '../types'
import { paramText } from '../utils'

const pct = (v: any) => (v == null ? '—' : `${(v * 100).toFixed(2)}%`)
const num2 = (v: any) => (v == null ? '—' : Number(v).toFixed(2))

export default function Settings() {
  const qc = useQueryClient()
  // 进度只体现在按下的那个按钮上（loading）；因此同时只允许起一个任务，其余按钮先禁用
  const [task, setTask] = useState<{ id: string; key: string } | null>(null)
  const { data: taskData } = useTask(task?.id ?? null)
  const running = taskData?.status === 'queued' || taskData?.status === 'running'
  const busy = (key: string) => ({
    loading: running && task?.key === key,
    disabled: running && task?.key !== key,
  })
  const [segModal, setSegModal] = useState<{ accountId: number; accountName: string } | null>(null)
  // 管理分段：就地改结束日（提前结束）/ 启停
  const [segListModal, setSegListModal] = useState<{ accountId: number; accountName: string } | null>(null)
  // 寻优折明细弹窗：点胜出参数表的任意一行打开（哪一折选了哪个参数、训练窗与样本外窗各是什么水平）
  const [foldModal, setFoldModal] = useState<OptWinner | null>(null)
  const [segErr, setSegErr] = useState<string | null>(null)
  const [segMsg, setSegMsg] = useState<string | null>(null)
  const [strategyKey, setStrategyKey] = useState(0)
  const [segKey, setSegKey] = useState(0)

  const strategies = useQuery({ queryKey: ['strategies', undefined], queryFn: () => listStrategies() })
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: listAccounts })
  const winners = useQuery({ queryKey: ['winners'], queryFn: getWinners })
  const walkFolds = useQuery({
    queryKey: ['walkFolds', foldModal?.opt_id],
    queryFn: () => getWalkFolds(foldModal!.opt_id),
    enabled: !!foldModal,
  })
  // 样本外拼接净值曲线（与 oos_* 指标同源）
  const walkOos = useQuery({
    queryKey: ['walkOos', foldModal?.opt_id],
    queryFn: () => getWalkOos(foldModal!.opt_id),
    enabled: !!foldModal,
  })
  // 成分列的 ETF 名称（与 ParamsForm 共用同一份缓存）
  const etfCodes = useQuery({ queryKey: ['codes'], queryFn: getCodes, staleTime: Infinity })
  const etfName = (c: string) => etfCodes.data?.find((x) => x.code === c)?.name
  // 管理分段弹窗里的分段列表（与模拟明细页共用缓存）
  const segList = useQuery({
    queryKey: ['segments', segListModal?.accountId],
    queryFn: () => listSegments(segListModal!.accountId),
    enabled: !!segListModal,
  })

  const refresh = () => qc.invalidateQueries()
  const afterDone = () => refresh()

  const onStrategy = async (v: ParamsValues) => {
    await createStrategy(v)          // end_date 留空 = 跟最新行情
    setStrategyKey((k) => k + 1)
    refresh()
  }

  const onSegment = async (v: ParamsValues) => {
    if (!segModal) return
    await createSegment(segModal.accountId, {
      strategy_name: v.strategy_name,
      strategy_method: v.strategy_method,
      period: v.period,
      lookback: v.lookback,
      min_w: v.min_w,
      max_w: v.max_w,
      long_window: v.long_window,
      short_window: v.short_window,
      codes: v.codes,
      start_date: v.start_date,
      end_date: v.end_date || '9999-12-31',
    })
    setSegModal(null)
    setSegKey((k) => k + 1)
    refresh()
  }

  const flipStrategy = async (r: Strategy, checked: boolean) => {
    await patchStrategy(r.strategy_id, { is_active: checked })
    refresh()
  }

  // 胜出参数表里的「有效」开关 / 删除 —— 作用在与该寻优批次对应的策略上；
  // 没写成策略的行（strategy_id 为空）不可操作。
  const flipWinner = async (r: OptWinner, checked: boolean) => {
    if (r.strategy_id == null) return
    await patchStrategy(r.strategy_id, { is_active: checked })
    refresh()
  }

  const delWinner = async (r: OptWinner) => {
    if (r.strategy_id == null) return
    await deleteStrategy(r.strategy_id)
    refresh()
  }

  // 改名同步、延期：改名由后端自动同步结果表；延期机制已取消（end_date 留空=跟最新行情）

  // 改结束日 = 这一段跑到该日为止。后端只作废「新旧结束日里较早那天的次日」起的结果
  // （那天之前分段构成没变，历史行必然一样），之后回总览点「模拟刷新」按增量补算。
  const patchSeg = async (sid: number, end_date: string) => {
    const aid = segListModal?.accountId
    if (!aid) return
    try {
      setSegErr(null)
      const row = await patchSegment(aid, sid, { end_date })
      setSegMsg(row.invalidated_rows
        ? `已作废 ${row.recompute_from} 起的模拟结果（${row.invalidated_rows} 行）→ 去总览点「模拟刷新」补算`
        : `结束日已改为 ${end_date}（该日之前没有已算出的结果，无需重算）`)
      qc.invalidateQueries({ queryKey: ['segments', aid] })
      qc.invalidateQueries({ queryKey: ['accounts'] })
    } catch (err: any) {
      setSegErr(err?.response?.data?.detail ?? String(err))
    }
  }

  const delSeg = async (sid: number) => {
    const aid = segListModal?.accountId
    if (!aid) return
    try {
      setSegErr(null)
      await deleteSegment(aid, sid)
      setSegMsg('分段已删除 → 去总览点「模拟刷新」重算账户结果')
      qc.invalidateQueries({ queryKey: ['segments', aid] })
    } catch (err: any) {
      setSegErr(err?.response?.data?.detail ?? String(err))
    }
  }

  const segCols: TableColumnsType<SimSegment> = [
    { title: '策略名称', dataIndex: 'strategy_name', key: 'name', width: 190, render: (v) => v ?? '—' },
    { title: '参数', key: 'p', render: (_, r) => <span style={{ color: '#666' }}>{paramText(r)}</span> },
    { title: '开始日', dataIndex: 'start_date', key: 'sd', width: 115 },
    {
      title: '结束日',
      dataIndex: 'end_date',
      key: 'ed',
      width: 150,
      render: (v: string, r) => (
        <DatePicker
          size="small"
          value={dayjs(v)}
          allowClear={false}
          onChange={(d) => d && patchSeg(r.strategy_id, d.format('YYYY-MM-DD'))}
        />
      ),
    },
    {
      title: '操作',
      key: 'op',
      width: 90,
      render: (_, r) => (
        <Popconfirm title="删除该分段？" onConfirm={() => delSeg(r.strategy_id)}>
          <Button danger size="small">删除</Button>
        </Popconfirm>
      ),
    },
  ]

  // 成分：名称 + 代码都显示（名称主色、代码灰色小字），按项自动换行
  const codesCell = (v: string[]) => (
    <Space size={[12, 4]} wrap>
      {(v ?? []).map((c) => {
        const n = etfName(c)
        return (
          <span key={c}>
            {n ?? c}
            {n ? <span style={{ color: '#999', marginLeft: 4 }}>{c}</span> : null}
          </span>
        )
      })}
    </Space>
  )

  const strategyCols: TableColumnsType<Strategy> = [
    { title: '名称', dataIndex: 'strategy_name', key: 'name', width: 180, render: (v) => v ?? '—' },
    { title: '成分', dataIndex: 'codes', key: 'codes', render: codesCell },
    { title: '年化', dataIndex: 'ret_annual', key: 'ra', width: 100, render: pct },
    {
      title: '有效',
      dataIndex: 'is_active',
      key: 'active',
      width: 80,
      render: (v: boolean, r) => <Switch checked={v} onChange={(c) => flipStrategy(r, c)} />,
    },
    {
      title: '操作',
      key: 'op',
      width: 100,
      render: (_, r) => (
        <Popconfirm title="删除该策略？" onConfirm={async () => { await deleteStrategy(r.strategy_id); refresh() }}>
          <Button danger size="small">删除</Button>
        </Popconfirm>
      ),
    },
  ]

  const accountCols: TableColumnsType<SimAccount> = [
    { title: '账户', dataIndex: 'account_name', key: 'name', render: (v) => v ?? '—' },
    { title: '初始资金', dataIndex: 'init_cash', key: 'cash', render: (v) => Number(v).toLocaleString() },
    { title: '起止', key: 'range', render: (_, r) => `${r.start_date} ~ ${r.end_date ?? '最新'}` },
    {
      title: '操作',
      key: 'op',
      render: (_, r) => (
        <Space>
          <Button size="small" onClick={() => setSegModal({ accountId: r.account_id, accountName: r.account_name ?? '' })}>
            ＋分段
          </Button>
          <Button size="small" onClick={() => { setSegErr(null); setSegMsg(null); setSegListModal({ accountId: r.account_id, accountName: r.account_name ?? '' }) }}>
            管理分段
          </Button>
          <Popconfirm title="删除该账户？" onConfirm={async () => { await deleteAccount(r.account_id); refresh() }}>
            <Button danger size="small">删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const winnerCols: TableColumnsType<OptWinner> = [
    // 策略名由后端按 bt_params.opt_id 反查（胜出参数会写成那条策略）；没写成策略时显示 —
    { title: '策略名称', dataIndex: 'strategy_name', key: 'name', width: 170, render: (v) => v ?? '—' },
    {
      title: '参数', dataIndex: 'params', key: 'p',
      render: (v, r) => paramText({ ...v, strategy_method: r.strategy_method }),
    },
    { title: '年化', dataIndex: 'ret_annual', key: 'ra', width: 92, render: pct },
    { title: '回撤', dataIndex: 'max_drawdown', key: 'dd', width: 92, render: pct },
    { title: '夏普', dataIndex: 'sharpe', key: 'sh', width: 80, render: num2 },
    { title: '卡玛', dataIndex: 'calmar', key: 'ca', width: 80, render: num2 },
    { title: '索提诺', dataIndex: 'sortino', key: 'so', width: 86, render: num2 },
    {
      title: '有效',
      dataIndex: 'is_active',
      key: 'active',
      width: 80,
      render: (v: boolean | null | undefined, r) => (
        <span onClick={(e) => e.stopPropagation()}>
          <Switch checked={!!v} disabled={r.strategy_id == null} onChange={(c) => flipWinner(r, c)} />
        </span>
      ),
    },
    {
      title: '操作',
      key: 'op',
      width: 90,
      render: (_, r) => (
        <span onClick={(e) => e.stopPropagation()}>
          <Popconfirm title="删除该策略？" disabled={r.strategy_id == null} onConfirm={() => delWinner(r)}>
            <Button danger size="small" disabled={r.strategy_id == null}>删除</Button>
          </Popconfirm>
        </span>
      ),
    },
  ]

  const onAccount = async (raw: any) => {
    await createAccount({
      account_name: raw.account_name,
      init_cash: raw.init_cash,
      start_date: (raw.start_date as Dayjs).format('YYYY-MM-DD'),
      end_date: null,                     // 账户恒跟行情最新交易日（表单不再让填结束日）
      benchmark: raw.benchmark || 'sh510300',
      is_active: raw.is_active ?? true,
    })
    refresh()
  }

  const [optForm] = Form.useForm()

  const startTask = (key: string, fn: () => Promise<{ task_id: string }>) => {
    fn().then((r) => setTask({ id: r.task_id, key }))
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {task ? <TaskNotice taskId={task.id} onDone={afterDone} /> : null}

      <Tabs
        items={[
          {
            key: 'data',
            label: '数据更新',
            children: (
              <Space direction="vertical">
                <Space>
                  <Button type="primary" {...busy('sync')} onClick={() => startTask('sync', syncMarket)}>同步行情</Button>
                  <Button {...busy('div')} onClick={() => startTask('div', generateDividends)}>生成分红/折算</Button>
                </Space>
                <div style={{ color: '#999' }}>建议顺序：先同步行情，再生成分红/折算，然后 回测刷新 / 寻优刷新 / 模拟刷新。</div>
              </Space>
            ),
          },
          {
            key: 'bt',
            label: '回测策略',
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <Card title="新增策略" size="small">
                  <ParamsForm key={strategyKey} variant="strategy" onSubmit={onStrategy} />
                </Card>
                <Card title="策略列表" size="small">
                  <Table rowKey="strategy_id" columns={strategyCols} dataSource={strategies.data ?? []} size="small" />
                </Card>
              </div>
            ),
          },
          {
            key: 'opt',
            label: '寻优',
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <Card title="执行寻优" size="small">
                  <Form form={optForm} layout="inline" initialValues={{ workers: 0 }}>
                    <Form.Item name="workers" label="进程数(0=CPU核数)">
                      <InputNumber min={0} />
                    </Form.Item>
                    <Button
                      type="primary"
                      {...busy('opt')}
                      onClick={() => startTask('opt', () => runOptimize(optForm.getFieldsValue()))}
                    >
                      寻优刷新
                    </Button>
                  </Form>
                </Card>
                <Card title="胜出参数" size="small">
                  <div style={{ color: '#999', marginBottom: 8 }}>
                    滚动窗口 + 样本外验证：每折在训练窗里跑完参数网格选参数，再用它在紧接的样本外窗跑一遍，各折样本外段拼成一条净值 ——
                    表里的指标就是这条样本外拼接净值算出来的。
                    <b>点任意一行</b>打开该批次的折明细弹窗（里面是该批次的样本外拼接净值曲线；单段批次没有折，不响应点击）。
                  </div>
                  <ConfigProvider theme={{ components: { Table: { fontSize: 12 } } }}>
                    <Table
                      rowKey="opt_id"
                      columns={winnerCols}
                      dataSource={(winners.data ?? []).filter((w) => w.strategy_name != null)}
                      size="small"
                      // 点行开折明细弹窗；单段批次没有折（folds 为空）→ 该行不可点
                      onRow={(r) => (r.folds
                        ? { onClick: () => setFoldModal(r), style: { cursor: 'pointer' } }
                        : {})}
                    />
                  </ConfigProvider>
                </Card>
              </div>
            ),
          },
          {
            key: 'sim',
            label: '模拟',
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <Card title="新增模拟账户" size="small">
                  <Form layout="vertical" onFinish={onAccount} initialValues={{ init_cash: 1000000, benchmark: 'sh510300', is_active: true }}>
                    <Space wrap>
                      <Form.Item name="account_name" label="账户名"><Input style={{ width: 160 }} /></Form.Item>
                      <Form.Item name="init_cash" label="初始资金" rules={[{ required: true }]}><InputNumber min={10000} step={10000} /></Form.Item>
                      <Form.Item name="start_date" label="开始日" rules={[{ required: true }]}><DatePicker /></Form.Item>
                      <Form.Item name="benchmark" label="基准"><Input style={{ width: 120 }} /></Form.Item>
                      <Form.Item name="is_active" label="有效" valuePropName="checked"><Switch /></Form.Item>
                    </Space>
                    <Button type="primary" htmlType="submit">新增账户</Button>
                  </Form>
                </Card>
                <Card title="账户列表" size="small">
                  <Table rowKey="account_id" columns={accountCols} dataSource={accounts.data ?? []} size="small" />
                </Card>
              </div>
            ),
          },
        ]}
      />

      <Modal
        title={`给账户「${segModal?.accountName}」加分段`}
        open={!!segModal}
        footer={null}
        onCancel={() => setSegModal(null)}
      >
        <ParamsForm key={segKey} variant="segment" onSubmit={onSegment} />
      </Modal>

      <Modal
        title={`账户「${segListModal?.accountName}」的策略分段`}
        open={!!segListModal}
        footer={null}
        width={900}
        onCancel={() => setSegListModal(null)}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="改「结束日」= 这一段跑到该日为止（提前结束，之后只持股估值）。该日之后的模拟结果会自动作废，回总览点「模拟刷新」补算；该日之前的历史原样保留。"
        />
        {segMsg ? <Alert type="success" showIcon style={{ marginBottom: 12 }} message={segMsg} /> : null}
        {segErr ? <Alert type="error" showIcon style={{ marginBottom: 12 }} message={segErr} /> : null}
        <Table
          rowKey="strategy_id"
          columns={segCols}
          dataSource={segList.data ?? []}
          size="small"
          pagination={false}
          loading={segList.isLoading}
        />
      </Modal>

      {/* 折明细弹窗：只画样本外拼接净值曲线（点胜出参数表的行打开）。
          曲线需要 opt_folds 才能标折分界、悬停显示该折参数与成绩，所以两个接口都要取。 */}
      <Modal
        title={`寻优 #${foldModal?.opt_id ?? ''} 样本外拼接净值曲线${foldModal?.strategy_name ? `（${foldModal.strategy_name}）` : ''}`}
        open={!!foldModal}
        footer={null}
        width={1240}
        onCancel={() => setFoldModal(null)}
      >
        <div style={{ color: '#999', marginBottom: 4 }}>
          各折用自己的胜出参数跑紧接的样本外窗，再首尾相接拼成这一条净值；
          竖虚线 = 折分界（参数切换点），鼠标悬停看该折选出的参数与该折样本外成绩，可框选放大。
        </div>
        <OosCurve
          points={walkOos.data ?? []}
          folds={walkFolds.data ?? []}
          method={foldModal?.strategy_method ?? ''}
          height={420}
        />
      </Modal>
    </div>
  )
}