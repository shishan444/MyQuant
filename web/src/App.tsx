import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
} from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppLayout } from "@/components/layout/AppLayout";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Lab } from "@/pages/Lab";
import { Strategies } from "@/pages/Strategies";
import { Verify } from "@/pages/Verify";
import { Trading } from "@/pages/Trading";
import { DataManagement } from "@/pages/DataManagement";
import { Settings } from "@/pages/Settings";
import { Plugins } from "@/pages/Plugins";
import { Workbench } from "@/pages/Workbench";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
  },
});

const router = createBrowserRouter([
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <Navigate to="/workbench" replace /> },
      { path: "/lab", element: <ErrorBoundary><Lab /></ErrorBoundary> },
      { path: "/strategies", element: <ErrorBoundary><Strategies /></ErrorBoundary> },
      { path: "/verify", element: <ErrorBoundary><Verify /></ErrorBoundary> },
      { path: "/trading", element: <ErrorBoundary><Trading /></ErrorBoundary> },
      { path: "/data", element: <ErrorBoundary><DataManagement /></ErrorBoundary> },
      { path: "/settings", element: <ErrorBoundary><Settings /></ErrorBoundary> },
      { path: "/workbench", element: <ErrorBoundary><Workbench /></ErrorBoundary> },
      { path: "/plugins", element: <ErrorBoundary><Plugins /></ErrorBoundary> },
      // catch-all：退役路由（/evolution /batch-backtest /live 等）与未知
      // 路径统一重定向工作台（U10——无死链接白屏）
      { path: "*", element: <Navigate to="/workbench" replace /> },
    ],
  },
]);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
