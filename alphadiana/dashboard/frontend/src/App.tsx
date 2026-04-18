import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { ConfigProvider, theme, Layout, Menu, App as AntdApp } from 'antd';
import {
  DashboardOutlined,
  ExperimentOutlined,
  PlusCircleOutlined,
} from '@ant-design/icons';
import RunsListPage from './pages/RunsListPage';
import RunDetailPage from './pages/RunDetailPage';
import ComparePage from './pages/ComparePage';
import JobsPage from './pages/JobsPage';
import NewJobPage from './pages/NewJobPage';

const { Header, Content } = Layout;

function NavHeader() {
  const navigate = useNavigate();
  const location = useLocation();

  const currentKey = location.pathname.startsWith('/jobs/new')
    ? '/jobs/new'
    : location.pathname.startsWith('/jobs')
    ? '/jobs'
    : '/';

  return (
    <Header
      style={{
        display: 'flex',
        alignItems: 'center',
        background: 'linear-gradient(90deg, #0a1628 0%, #001529 100%)',
        padding: '0 28px',
        gap: 28,
        height: 56,
        borderBottom: '1px solid rgba(255,255,255,0.06)',
      }}
    >
      <span style={{
        color: '#fff',
        fontSize: 24,
        fontWeight: 700,
        whiteSpace: 'nowrap',
        letterSpacing: '-0.3px',
        fontFamily: "'Inter', sans-serif",
      }}>
        AlphaDiana
      </span>
      <Menu
        theme="dark"
        mode="horizontal"
        selectedKeys={[currentKey]}
        onClick={({ key }) => navigate(key)}
        style={{
          flex: 1,
          minWidth: 0,
          background: 'transparent',
          borderBottom: 'none',
          fontFamily: "'Inter', sans-serif",
          fontSize: 14,
          fontWeight: 500,
        }}
        items={[
          { key: '/', icon: <DashboardOutlined />, label: 'Results' },
          { key: '/jobs', icon: <ExperimentOutlined />, label: 'Jobs' },
          { key: '/jobs/new', icon: <PlusCircleOutlined />, label: 'New Eval' },
        ]}
      />
    </Header>
  );
}

export default function App() {
  return (
    <ConfigProvider
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: '#1677ff',
          fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
          fontSize: 14,
          borderRadius: 8,
          colorBgContainer: '#ffffff',
          colorBgLayout: '#f0f2f5',
        },
        components: {
          Table: { fontSize: 13 },
          Statistic: { titleFontSize: 13 },
        },
      }}
    >
      <AntdApp>
      <BrowserRouter>
        <Layout style={{ minHeight: '100vh' }}>
          <NavHeader />
          <Content style={{ padding: '20px 24px', background: '#f0f2f5' }}>
            <Routes>
              <Route path="/" element={<RunsListPage />} />
              <Route path="/runs/:runId" element={<RunDetailPage />} />
              <Route path="/compare" element={<ComparePage />} />
              <Route path="/jobs" element={<JobsPage />} />
              <Route path="/jobs/new" element={<NewJobPage />} />
            </Routes>
          </Content>
        </Layout>
      </BrowserRouter>
      </AntdApp>
    </ConfigProvider>
  );
}
