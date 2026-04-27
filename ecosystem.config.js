// PM2 ecosystem file for ivo.
//
// PM2 is *optional*. The bot runs perfectly fine via `python -m ivo`
// or `make run`. This file is shipped as a convenience for operators who already
// use PM2 to supervise long-running processes.
//
// Usage:
//   pm2 start ecosystem.config.js
//   pm2 save
//   pm2 logs ivo
//
// Notes:
//   - Adjust `cwd` and `interpreter` if your venv lives elsewhere.
//   - Secrets come from .env (loaded by the app), not from this file.

module.exports = {
  apps: [
    {
      name: "ivo",
      cwd: __dirname,
      script: __dirname + "/.venv/bin/python",
      args: "-m ivo --config config.yaml",
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      min_uptime: "10s",
      kill_timeout: 10000,
      restart_delay: 2000,
      watch: false,
      env: {
        PYTHONUNBUFFERED: "1"
      },
    },
  ],
};
