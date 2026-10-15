import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'
import { tmpdir } from 'node:os'

const root = resolve(import.meta.dirname, '..')
const python = process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python'
process.env.TG_E2E_DATA = process.env.TG_E2E_DATA || resolve(tmpdir(), `tg-search-e2e-${Date.now()}`)

export default defineConfig({
  testDir: './tests', workers: 1, timeout: 60000,
  use: { baseURL: 'http://127.0.0.1:18787', browserName: 'chromium', viewport: { width: 1440, height: 1000 } },
  webServer: {
    command: `"${python}" -m app.main serve --port 18787`, cwd: root,
    url: 'http://127.0.0.1:18787/healthz', reuseExistingServer: false,
    env: { TG_DATA_DIR: process.env.TG_E2E_DATA, TG_PUBLIC_ORIGIN: 'http://127.0.0.1:18787' },
  },
})
