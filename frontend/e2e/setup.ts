import { spawnSync } from "node:child_process";
export default function setup() {
  for (const args of [
    ["-m", "alembic", "upgrade", "head"],
    ["-m", "app.seed"],
  ]) {
    const result = spawnSync("../.venv/bin/python", args, {
      cwd: "../backend",
      env: process.env,
      encoding: "utf8",
    });
    if (result.status !== 0) throw new Error(result.stderr || result.stdout);
  }
}
