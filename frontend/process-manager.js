import { app, dialog } from 'electron';
import path from 'path';
import { fileURLToPath } from 'url';
import { execSync, spawn } from 'child_process';
import fs from 'fs';
import log from 'electron-log/main.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export class ProcessManager {
  constructor() {
    this.isShuttingDown = false;
    this.heartbeatTimer = null;
    this.HEALTH_URL = "http://127.0.0.1:8000/health";
    this.pm2Name = "no0_ai_backend";
    this.backendProcess = null;
  }

  _isPackaged() {
    // 只信 Electron 官方状态，避免打包后环境变量/工作目录误判为开发模式。
    return app.isPackaged === true;
  }

  _isPm2Running() {
    try {
      const output = execSync(`pm2 jlist`, { encoding: 'utf-8' });
      const list = JSON.parse(output);
      return list.some(p => p.name === this.pm2Name && p.pm2_env.status === 'online');
    } catch (e) {
      return false;
    }
  }

  startBackend() {
    if (this.isShuttingDown) return;
    const isPackaged = this._isPackaged();
    // 发布模式绝不探测、调用或依赖 PM2/Node CLI。
    if (!isPackaged && this._isPm2Running()) {
      log.info("Backend process already running via PM2, skip spawn");
      return;
    }

    try {
      const isWindows = process.platform === 'win32';
      if (isWindows) {
        log.info("Checking and killing any process on port 8000...");
        execSync('for /f "tokens=5" %a in (\'netstat -aon ^| findstr :8000\') do taskkill /F /PID %a', { stdio: 'ignore' });
        log.info("Port 8000 cleaned up.");
      }
    } catch (e) {
      // ignore
    }

    const isWindows = process.platform === 'win32';
    if (!isWindows) {
      log.warn('Platform is not Windows, skipping backend spawn.');
      return;
    }
    log.info(`--- New Session ---`);
    log.info(`startBackend called. isPackaged=${isPackaged}`);
    let backendDir = path.join(__dirname, '..');

    if (isPackaged) {
      const exeDir = path.dirname(app.getPath('exe'));
      log.info(`exeDir=${exeDir}`);

      const candidates = [
        path.join(process.resourcesPath, 'No0_AI_V4.exe'),
        path.join(exeDir, 'No0_AI_V4_Backend', 'No0_AI_V4_Backend.exe'),
        path.join(exeDir, 'No0_AI_V4.exe'),
        path.join(exeDir, 'No0_AI_V4_Backend.exe'),
        path.join(exeDir, '..', '..', '..', 'dist', 'No0_AI_V4.exe')
      ];

      let foundBackend = null;
      for (const c of candidates) {
        if (fs.existsSync(c)) {
          foundBackend = c;
          break;
        }
      }

      if (foundBackend) {
        log.info(`Found PyInstaller backend: ${foundBackend}`);
        try {
          this.backendProcess = spawn(foundBackend, [], {
            cwd: path.dirname(foundBackend),
            env: { ...process.env, ELECTRON_BACKEND: "1" },
            windowsHide: true,
            detached: false
          });
          this.backendProcess.once('error', (error) => {
            log.error(`Built backend spawn error: ${error}`);
          });
          this.backendProcess.once('exit', (code, signal) => {
            if (!this.isShuttingDown && code !== 0) {
              log.error(`Built backend exited unexpectedly: code=${code}, signal=${signal}`);
            }
          });
          log.info(`Spawned built backend directly.`);
          this.startHeartbeatCheck();
        } catch (e) {
          log.error(`Error spawning built backend directly: ${e}`);
        }
        return;
      }
      log.error('Packaged backend executable was not found; refusing PM2/development fallback.');
      dialog.showErrorBox(
        '后端启动失败',
        '未找到 No0_AI_V4.exe。请重新安装完整版本，或检查安装包的 resources 目录。'
      );
      return;
    }

    // 仅开发环境：优先调用 PM2；PM2 不可用时直接 spawn Python，仍不影响打包模式。
    const venvPython = path.join(backendDir, '.venv', 'Scripts', 'python.exe');
    const pythonExe = fs.existsSync(venvPython) ? venvPython : 'python';

    try {
      log.info(`Starting backend using Python via PM2: ${pythonExe}`);
      const pm2Cmd = `pm2 start "${pythonExe}" --name "${this.pm2Name}" --restart-delay 3000 -- -m uvicorn main:app --host 127.0.0.1 --port 8000`;
      execSync(pm2Cmd, {
        cwd: backendDir,
        env: { ...process.env, ELECTRON_BACKEND: "1" }
      });
      log.info(`Spawned via PM2`);
      this.startHeartbeatCheck();
    } catch (e) {
      log.warn(`PM2 unavailable, fallback to direct Python spawn: ${e}`);
      try {
        this.backendProcess = spawn(pythonExe, ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000'], {
          cwd: backendDir,
          env: { ...process.env, ELECTRON_BACKEND: "1" },
          windowsHide: true,
          detached: false
        });
        this.backendProcess.once('error', (error) => {
          log.error(`Python backend spawn error: ${error}`);
        });
        this.startHeartbeatCheck();
      } catch (fallbackError) {
        log.error(`Error spawning Python backend directly: ${fallbackError}`);
      }
    }
  }

  startHeartbeatCheck() {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = setInterval(async () => {
      const backendLost = this._isPackaged()
        ? !this.backendProcess || this.backendProcess.killed || this.backendProcess.exitCode !== null
        : !this._isPm2Running() && (!this.backendProcess || this.backendProcess.exitCode !== null);
      if (backendLost) {
        log.warn("Heartbeat detect: PM2 backend lost, trigger restart");
        this.startBackend();
        return;
      }

      try {
        const res = await fetch(this.HEALTH_URL, {
          signal: AbortSignal.timeout(3000)
        });
        if (!res.ok) throw new Error(`health status ${res.status}`);
      } catch (err) {
        log.warn("Health-check failed, will let PM2 restart backend", err);
      }
    }, 5000);
  }

  stopBackend() {
    this.isShuttingDown = true;
    this.killBackend();
  }

  killBackend() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
    if (this._isPackaged()) {
      if (this.backendProcess && !this.backendProcess.killed) {
        try {
          this.backendProcess.kill();
          log.info("Stopped packaged backend process directly");
        } catch (e) {
          log.error("kill packaged backend error", e);
        }
      }
      this.backendProcess = null;
      this.isShuttingDown = false;
      return;
    }
    try {
      execSync(`pm2 stop ${this.pm2Name}`);
      execSync(`pm2 delete ${this.pm2Name}`);
      log.info(`Stopped and deleted PM2 backend process`);
    } catch (e) {
      // 开发环境 PM2 不存在时，回收 direct-spawn fallback。
      if (this.backendProcess && !this.backendProcess.killed) {
        try { this.backendProcess.kill(); } catch (fallbackError) {
          log.error("kill fallback backend error", fallbackError);
        }
      }
      this.backendProcess = null;
      log.warn("kill backend via PM2 skipped/failed", e);
    }
    this.isShuttingDown = false;
  }
}

export const processManager = new ProcessManager();