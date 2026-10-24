# React integration workspace

React + TypeScript + Vite, Tailwind CSS, Radix/shadcn-style source components,
React Router, TanStack Query, Vitest, and Playwright.

Use Node.js 22.12+ within 22.x, or 24+, on PATH and install the exact npm lock with `npm ci`.
See the [root setup guide](../README.md) for PostgreSQL port 55432, migrations
through 0008, backend authentication, and the independent durable worker.

```bash
npm ci
npm run dev
```

Open http://127.0.0.1:5173 and use **Connect API** with the privately configured
local development token. Vite proxies `/api` to http://127.0.0.1:8000.
Never put keys, passwords, bearer tokens, or database URLs in `VITE_*` values.
The dashboard and history load backend data; diagnostic-contract administration
remains unavailable without trusted server-injected providers. Endpoint approval
and diagnostic-contract approval are separate.

```bash
npm test
npx playwright install chromium
npm run test:browser
npm run build
```

Chromium fixture tests mock HTTP responses; they are not live-model tests.
The Playwright server requires port 5173 free. `npm run preview` serves the build
but is not the development API proxy. Wrapper completion on the timeline does
not establish a model call or successful validation. See the root guide for
limitations and the separate local live acceptance harness prerequisites.
