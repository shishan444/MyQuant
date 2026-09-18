import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ConfigProvider, App as AntdApp } from "antd";
import zhCN from "antd/locale/zh_CN";
import "./index.css";
import { App } from "./App";
import { antdThemeConfig } from "./theme/antdTheme";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ConfigProvider theme={antdThemeConfig} locale={zhCN}>
      {/* AntdApp 使 App.useApp().modal/message/notification 可用（确认交互单源=AntD 体系） */}
      <AntdApp>
        <App />
      </AntdApp>
    </ConfigProvider>
  </StrictMode>
);
