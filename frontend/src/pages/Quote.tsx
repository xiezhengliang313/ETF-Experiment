import { useState } from 'react'
import { Button, Card, Input, Space, Table } from 'antd'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { TableColumnsType } from 'antd'
import { getQuote, runQuote } from '../api/market'
import { TaskNotice } from '../components/TaskNotice'
import { useTask } from '../hooks/useTask'
import type { Quote } from '../types'

const GRAY = '#8c8c8c'
const f3 = (v: any) => (v == null ? '—' : Number(v).toFixed(3))
// 涨跌幅/折溢价/区间涨跌幅：正红负绿（A 股习惯），带正号
const ret = (v: any) => {
  if (v == null) return '—'
  const color = v > 0 ? '#cf1322' : v < 0 ? '#3f8600' : undefined
  return <span style={{ color }}>{Number(v) > 0 ? '+' : ''}{Number(v).toFixed(2)}%</span>
}
const pct = (v: any) => (v == null ? '—' : `${Number(v).toFixed(2)}%`)
const f2 = (v: any) => (v == null ? '—' : Number(v).toFixed(2))
const yi = (v: any) => (v == null ? '—' : `${Number(v).toFixed(2)} 亿`)
const wan2yi = (v: any) => (v == null ? '—' : `${(Number(v) / 10000).toFixed(2)} 亿`)   // 成交额存的是万元
const ts = (v: any) => (v == null ? '—' : String(v).replace('T', ' ').slice(0, 19))

// 只展示这 17 列（库里还有五档盘口/内外盘/涨跌停/股本等列，接口照样在返，需要时再往这里加）
// 前 6 列 fixed: 'left' 横向滑动时一直贴在左边（固定列必须给 width，否则偏移算不出来）
const columns: TableColumnsType<Quote> = [
  { title: '代码', dataIndex: 'code', fixed: 'left', width: 68 },
  { title: '名称', dataIndex: 'name', fixed: 'left', width: 108, ellipsis: true },
  { title: '最新价', dataIndex: 'last', fixed: 'left', width: 58, align: 'right', render: f3 },
  { title: '净值', dataIndex: 'nav', fixed: 'left', width: 58, align: 'right', render: f3 },
  { title: '成交额', dataIndex: 'amount_wan', fixed: 'left', width: 66, align: 'right', render: wan2yi },
  { title: '总市值', dataIndex: 'market_cap', fixed: 'left', width: 74, align: 'right', render: yi },
  // 默认按涨跌幅倒序（点表头可切换升/降序）
  {
    title: '涨跌幅', dataIndex: 'change_pct', align: 'right', render: ret,
    sorter: (a, b) => (a.change_pct ?? -9999) - (b.change_pct ?? -9999),
    defaultSortOrder: 'descend',
  },
  { title: '折溢价率', dataIndex: 'premium', align: 'right', render: ret },
  { title: '换手率', dataIndex: 'turnover_pct', align: 'right', render: pct },
  { title: '量比', dataIndex: 'volume_ratio', align: 'right', render: f2 },
  { title: '年初至今', dataIndex: 'ret_ytd', align: 'right', render: ret },
  { title: '近5日', dataIndex: 'ret_5d', align: 'right', render: ret },
  { title: '近10日', dataIndex: 'ret_10d', align: 'right', render: ret },
  { title: '近20日', dataIndex: 'ret_20d', align: 'right', render: ret },
  { title: '近60日', dataIndex: 'ret_60d', align: 'right', render: ret },
  { title: '52周最高', dataIndex: 'high_52w', align: 'right', render: f3 },
  { title: '52周最低', dataIndex: 'low_52w', align: 'right', render: f3 },
]

// 实时行情页：读库里的快照（etf_quote），按钮触发一次抓取（走后台任务）
export default function QuotePage() {
  const qc = useQueryClient()
  const [task, setTask] = useState<string | null>(null)
  const { data: taskData } = useTask(task)
  const running = taskData?.status === 'queued' || taskData?.status === 'running'
  const [kw, setKw] = useState('')

  const quote = useQuery({ queryKey: ['quote'], queryFn: () => getQuote() })
  const rows = (quote.data?.rows ?? []).filter(
    (r) => !kw || r.code.includes(kw) || (r.name ?? '').includes(kw),
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card
        title="实时行情（ETF 当日）"
        size="small"
        extra={
          <Space>
            <Input
              allowClear
              placeholder="过滤代码/名称"
              style={{ width: 180 }}
              onChange={(e) => setKw(e.target.value.trim())}
            />
            <Button
              type="primary"
              loading={running}
              onClick={() => runQuote().then((r) => setTask(r.task_id))}
            >
              抓取实时行情
            </Button>
          </Space>
        }
      >
        <div style={{ fontSize: 12, color: GRAY, marginBottom: 8 }}>
          数据源：腾讯 qt.gtimg.cn（GBK），标的取 etf_bars_raw 全部；主键含行情时间，
          盘中每隔几分钟抓一次会各留一行，这里每个标的只显示最新那条。
          {quote.data?.trade_date
            ? `　当前快照：交易日 ${quote.data.trade_date} · ${quote.data.count} 只 · 最新行情时间 ${ts(quote.data.quote_time)}`
            : '　库里还没有快照，点右上角按钮抓一次。'}
        </div>
        {/* 表体限高 450px = 15 行（窗口矮时按视口收缩）：表头钉在表格顶部不随滚动走，
            横向滚动条也落在表格底部。.quote-table（见 index.css）把行高收到 ~30px */}
        <div className="quote-table">
          <Table<Quote>
            rowKey="code"
            columns={columns}
            dataSource={rows}
            loading={quote.isLoading}
            size="small"
            scroll={{ x: 'max-content', y: 'min(calc(100vh - 190px), 450px)' }}
            pagination={false}
          />
        </div>
      </Card>

      {task ? <TaskNotice taskId={task} onDone={() => qc.invalidateQueries({ queryKey: ['quote'] })} /> : null}
    </div>
  )
}
