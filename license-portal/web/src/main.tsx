import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import 'antd/dist/reset.css'
import './styles/app.css'

import App from './App'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 15_000,
    },
    mutations: {
      retry: 0,
    },
  },
})

const root = document.getElementById('root')
if (!root) throw new Error('missing #root element')

createRoot(root).render(
  <StrictMode>
    <ConfigProvider
      locale={zhCN}
      button={{ autoInsertSpace: false }}
      theme={{
        token: {
          colorPrimary: '#2b5ce6',
          colorInfo: '#2b5ce6',
          colorSuccess: '#067647',
          colorWarning: '#b54708',
          colorError: '#c4291b',
          colorText: '#0b0f18',
          colorTextSecondary: '#5c6675',
          colorBorder: '#e4e8ee',
          colorBgContainer: '#ffffff',
          borderRadius: 8,
          controlHeight: 40,
          fontSize: 14,
          fontFamily:
            'Inter, "Segoe UI", -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
        },
        components: {
          Button: {
            fontWeight: 550,
            primaryShadow: 'none',
            defaultShadow: 'none',
          },
          Table: {
            headerBg: '#fafbfd',
            headerSplitColor: 'transparent',
            cellPaddingBlock: 12,
          },
          Input: {
            paddingBlock: 7,
          },
          Modal: {
            titleFontSize: 16,
          },
        },
      }}
    >
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  </StrictMode>,
)
