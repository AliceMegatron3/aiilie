import { app, BrowserWindow, ipcMain, dialog } from 'electron';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';
import log from 'electron-log/main.js';
import { processManager } from './process-manager.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

log.initialize();

// 批次3:预加载脚本路径(向渲染层暴露受控的文件夹选择能力)
const preloadPath = path.join(__dirname, 'preload.cjs');

function createWindow() {
  const mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    webPreferences: {
      // 安全加固：关闭 Node 集成并开启上下文隔离，防止 XSS 升级为 RCE
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      preload: preloadPath
    }
  });

  const devUrl = process.env.NO0_ELECTRON_DEV_URL;
  if (!app.isPackaged && devUrl) {
    log.info(`Loading Electron development URL: ${devUrl}`);
    mainWindow.loadURL(devUrl).catch((error) => {
      log.error('Failed to load Electron development URL', error);
    });
  } else {
    log.info('Loading packaged frontend from dist/index.html');
    mainWindow.loadFile(path.join(__dirname, 'dist', 'index.html'));
  }
}

// 批次3:文件夹选择 IPC——渲染层通过 contextBridge 调 dialog.showOpenDialog
// 仅返回所选目录绝对路径,不提供任意文件系统访问能力。
ipcMain.handle('select-folder', async () => {
  const result = await dialog.showOpenDialog({
    title: '选择文件夹',
    properties: ['openDirectory'],
  });
  if (result.canceled || !result.filePaths.length) {
    return null;
  }
  return result.filePaths[0];
});

app.whenReady().then(() => {
  log.info("Electron app ready, starting backend process");
  processManager.startBackend();
  createWindow();

  app.on('activate', function () {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', function () {
  if (process.platform !== 'darwin') app.quit();
});

// 使用 before-quit 保证退出前可以终止后端进程
// 修复：事件名原为 U+2011 非断行连字符（'before‑quit'），Electron 无法识别该事件，
// 导致退出时无法回调 processManager.stopBackend()，后端进程残留。
// 现改为标准 ASCII 连字符 'before-quit'。
app.on('before-quit', () => {
  log.info("App before-quit event, terminating backend...");
  processManager.stopBackend();
});

// 捕获主进程未捕获异常，写入临时日志
process.on('uncaughtException', (error) => {
  try {
    const logPath = path.join(app.getPath('temp'), 'no0_ai_startup.log');
    fs.appendFileSync(logPath, `[UncaughtException] ${new Date().toISOString()} ${error.stack}\n`);
  } catch (e) {
    log.error("Failed write crash log", e);
  }
  log.error('[UncaughtException]', error);
});
