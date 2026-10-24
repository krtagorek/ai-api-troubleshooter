import { defineConfig } from '@playwright/test'

const port = Number(process.env.PLAYWRIGHT_PORT ?? 5173)
if (!Number.isInteger(port) || port < 1024 || port > 65535) {
  throw new Error('PLAYWRIGHT_PORT must be an integer between 1024 and 65535')
}
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: './e2e', timeout: 20000, fullyParallel: true,
  use: { baseURL, browserName: 'chromium', trace: 'retain-on-failure' },
  webServer: {
    command: `npm run dev -- --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: false,
  },
})
