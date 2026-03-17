import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { ConfigProvider, theme, Layout, Menu } from 'antd';
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
        background: '#001529',
        padding: '0 24px',
        gap: 24,
      }}
    >
      <span style={{ color: '#fff', fontSize: 18, fontWeight: 700, whiteSpace: 'nowrap' }}>
        AlphaDiana
      </span>
      <Menu
        theme="dark"
        mode="horizontal"
        selectedKeys={[currentKey]}
        onClick={({ key }) => navigate(key)}
        style={{ flex: 1, minWidth: 0 }}
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
        token: { colorPrimary: '#1677ff' },
      }}
    >
      <BrowserRouter>
        <Layout style={{ minHeight: '100vh' }}>
          <NavHeader />
          <Content style={{ padding: '24px', background: '#f5f5f5' }}>
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
    </ConfigProvider>
  );
}
