import { Layout, Menu } from 'antd'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'

export default function App() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const selected = pathname.startsWith('/settings') ? '/settings' : '/'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* 侧栏常驻：sticky + 满屏高，滚动正文时左侧的标题和菜单不动 */}
      <Layout.Sider style={{ position: 'sticky', top: 0, height: '100vh', overflow: 'auto' }}>
        <div className="logo">ETF 分析系统</div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selected]}
          onClick={(e) => navigate(e.key)}
          items={[
            { key: '/', label: '总览' },
            { key: '/settings', label: '系统设置' },
          ]}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Content style={{ padding: 24 }}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  )
}