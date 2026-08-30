# CoursePilot Web

CoursePilot 的 Next.js 16 前端应用。当前包含应用骨架、产品启动页、同源 API 网关和 Web 进程健康检查。

## 本地运行

```bash
cd /path/to/coursepilot
pnpm install --frozen-lockfile
pnpm --filter @coursepilot/web dev
```

打开 `http://localhost:3000`。Web 健康检查位于 `GET /healthz`。

浏览器请求 `/api/*` 时，Next.js 会通过服务端网关转发到
`API_INTERNAL_URL`（本地默认 `http://127.0.0.1:8000`）。该地址不会暴露给浏览器；
Docker Compose 中应设置为 `http://api:8000`。

## 验证

```bash
pnpm --filter @coursepilot/web check
```

该命令依次执行 ESLint、TypeScript 类型检查和生产构建。
