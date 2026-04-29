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
//   - Logs go to the OS-conventional location and are rotated by the
//     `pm2-logrotate` module — install once with:
//         pm2 install pm2-logrotate
//         pm2 set pm2-logrotate:max_size 10M
//         pm2 set pm2-logrotate:retain 7
//         pm2 set pm2-logrotate:compress true

const path = require("path");
const os = require("os");

// macOS convention: ~/Library/Logs/<app>. Other OSes: ~/.local/state/<app>/log.
const LOG_DIR = process.platform === "darwin"
  ? path.join(os.homedir(), "Library", "Logs", "ivo")
  : path.join(os.homedir(), ".local", "state", "ivo", "log");

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
      out_file: path.join(LOG_DIR, "ivo.out.log"),
      error_file: path.join(LOG_DIR, "ivo.err.log"),
      merge_logs: true,
      time: true,
      env: {
        PYTHONUNBUFFERED: "1",
        LOG_LEVEL: "ERROR",
      },
    },
  ],
};
