import { defineConfig } from "@playwright/test";
const env = {
  JWT_SECRET: "e2e-only-jwt-secret-at-least-thirty-two-characters",
  SAHYOG_SERVICE_TOKEN: "e2e-only-service-token-at-least-24",
  DEMO_PASSWORD: "E2e-password-2026",
  SEED_DEMO: "true",
  DATABASE_URL: "sqlite:///../frontend/e2e.db",
  MOCK_DATABASE_URL: "sqlite:///../frontend/e2e-mock.db",
  UPLOAD_DIR: "../frontend/test-results/uploads",
  FRONTEND_ORIGIN: "http://localhost:5173",
  SAHYOG_URL: "http://localhost:8001",
  MOCK_VASP_URL: "http://localhost:8002",
  VASP_DATABASE_URL: "sqlite:///../frontend/e2e-vasps.db",
  MOCK_VASP_DELAY_SCALE: "0.1",
  FEDERATED_TIMEOUT_SECONDS: "0.3",
  JOB_BACKEND: "local",
  REPLAY_MODE: "true",
};
Object.assign(process.env, env);
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/setup.ts",
  workers: 1,
  use: {
    baseURL: "http://localhost:5173",
    launchOptions: process.env.CHROMIUM_PATH
      ? { executablePath: process.env.CHROMIUM_PATH }
      : {},
  },
  webServer: [
    {
      command: "../.venv/bin/python -m uvicorn app.main:app --port 8000",
      cwd: "../backend",
      url: "http://localhost:8000/health",
      env,
      timeout: 30000,
    },
    {
      command:
        "../.venv/bin/python -m uvicorn mock_services.sahyog.main:app --port 8001",
      cwd: "../backend",
      url: "http://localhost:8001/health",
      env: { ...env, PYTHONPATH: "..:." },
      timeout: 30000,
    },
    {
      command:
        "../.venv/bin/python -m uvicorn mock_services.vasps.main:app --port 8002",
      cwd: "../backend",
      url: "http://localhost:8002/health",
      env: { ...env, PYTHONPATH: "..:." },
      timeout: 30000,
    },
    {
      command: "npm run dev -- --port 5173",
      url: "http://localhost:5173",
      timeout: 30000,
    },
  ],
});
