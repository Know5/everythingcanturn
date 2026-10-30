import React from "react";
import { createRoot } from "react-dom/client";
import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { appTheme } from "./theme";
import App from "./App";
// 副作用 import：全局拖放保护（dragenter/dragover/drop 的 preventDefault）
// 与 window.onDrop/onJob 的注册必须在任何界面渲染前生效。
// 少了它，WebView2 会用默认行为把拖入的文件当导航目标打开。
import "./bridge";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={appTheme}>
      <AntApp>
        <App />
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);
