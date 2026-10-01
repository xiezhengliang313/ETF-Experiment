import { DatePicker, Form, Input, InputNumber, Select, Space, Switch, Button } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import { useQuery } from '@tanstack/react-query'
import { getCodes } from '../api/market'

export interface ParamsValues {
  strategy_name?: string
  strategy_method: string
  period: number
  lookback?: number
  min_w?: number
  max_w?: number
  long_window?: number
  short_window?: number
  codes: string[]
  start_date: string
  end_date?: string                       // 只有分段会填；留空 = 跑到账户结束（策略不填，恒跟最新行情）
  benchmark?: string
  is_active?: boolean
  note?: string
}

export function ParamsForm({ variant, onSubmit, submitting }: {
  variant: 'strategy' | 'segment'
  onSubmit: (values: ParamsValues) => void
  submitting?: boolean
}) {
  const [form] = Form.useForm()
  const method = Form.useWatch('strategy_method', form)
  const { data: codes = [] } = useQuery({ queryKey: ['codes'], queryFn: getCodes, staleTime: Infinity })

  const finish = (raw: any): void => {
    onSubmit({
      strategy_name: (raw.strategy_name as string) || undefined,
      strategy_method: raw.strategy_method as string,
      period: raw.period as number,
      lookback: raw.lookback as number | undefined,
      min_w: raw.min_w as number | undefined,
      max_w: raw.max_w as number | undefined,
      long_window: raw.long_window as number | undefined,
      short_window: raw.short_window as number | undefined,
      codes: raw.codes as string[],
      start_date: (raw.start_date as Dayjs).format('YYYY-MM-DD'),
      end_date: raw.end_date ? (raw.end_date as Dayjs).format('YYYY-MM-DD') : undefined,
      benchmark: (raw.benchmark as string) || 'sh510300',
      is_active: raw.is_active as boolean | undefined,
      note: (raw.note as string) || undefined,
    })
  }

  const isMeanVar = method === 'max_sharpe' || method === 'min_variance'

  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={{ strategy_method: 'max_sharpe', benchmark: 'sh510300', is_active: true }}
      onFinish={finish}
    >
      <Form.Item name="strategy_name" label="名称">
        <Input placeholder="可选" />
      </Form.Item>

      <Space wrap>
        <Form.Item name="strategy_method" label="策略方法" rules={[{ required: true }]}>
          <Select
            style={{ width: 160 }}
            options={[
              { value: 'max_sharpe', label: '最大夏普' },
              { value: 'min_variance', label: '最小方差' },
              { value: 'trend', label: '趋势轮动' },
            ]}
          />
        </Form.Item>
        <Form.Item name="period" label="调仓周期(交易日)" rules={[{ required: true }]}>
          <InputNumber min={1} />
        </Form.Item>
      </Space>

      <Space wrap>
        {isMeanVar ? (
          <>
            <Form.Item name="lookback" label="回看窗口" rules={[{ required: true }]}>
              <InputNumber min={1} />
            </Form.Item>
            <Form.Item name="min_w" label="最小权重" rules={[{ required: true }]}>
              <InputNumber min={0} max={1} step={0.05} />
            </Form.Item>
            <Form.Item name="max_w" label="最大权重" rules={[{ required: true }]}>
              <InputNumber min={0} max={1} step={0.05} />
            </Form.Item>
          </>
        ) : (
          <>
            <Form.Item name="long_window" label="长均线" rules={[{ required: true }]}>
              <InputNumber min={1} />
            </Form.Item>
            <Form.Item name="short_window" label="短均线" rules={[{ required: true }]}>
              <InputNumber min={1} />
            </Form.Item>
          </>
        )}
      </Space>

      <Form.Item name="codes" label="成分 ETF" rules={[{ required: true }]}>
        <Select
          mode="multiple"
          options={codes.map((c) => ({ value: c.code, label: `${c.code} ${c.name}` }))}
          placeholder="选择 2 只以上"
        />
      </Form.Item>

      <Space wrap>
        <Form.Item name="start_date" label="开始日" rules={[{ required: true }]}>
          <DatePicker />
        </Form.Item>
        {/* 策略不填结束日：end_date 恒为 NULL = 每次回测都跟行情最新交易日（填死日期反而要人工延期） */}
        {variant === 'segment' ? (
          <Form.Item name="end_date" label="结束日(留空=跑到账户结束)">
            <DatePicker />
          </Form.Item>
        ) : null}
      </Space>

      {variant === 'strategy' ? (
        <Space wrap>
          <Form.Item name="benchmark" label="基准">
            <Input style={{ width: 140 }} />
          </Form.Item>
          <Form.Item name="is_active" label="有效" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Space>
      ) : null}

      <Form.Item name="note" label="备注">
        <Input.TextArea rows={2} />
      </Form.Item>

      <Button type="primary" htmlType="submit" loading={submitting}>
        {variant === 'strategy' ? '新增策略' : '新增分段'}
      </Button>
    </Form>
  )
}