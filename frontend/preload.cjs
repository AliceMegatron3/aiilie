// 批次3:渲染层受控桥——仅暴露文件夹选择,不给任意文件系统访问权
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('desktopAPI', {
  /** 打开系统文件夹选择器;取消返回 null。仅返回路径字符串。 */
  selectFolder: () => ipcRenderer.invoke('select-folder'),
  /** 是否运行在 Electron 桌面环境(用于前端判断用选择器还是手动输入) */
  isDesktop: true,
});
