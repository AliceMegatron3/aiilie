import sys
import subprocess
import time
import socket
from pathlib import Path

def print_log(msg: str, status: str = "INFO"):
    colors = {
        "INFO": "\033[94m",
        "SUCCESS": "\033[92m",
        "WARNING": "\033[93m",
        "ERROR": "\033[91m",
        "ENDC": "\033[0m"
    }
    color = colors.get(status, colors["INFO"])
    print(f"{color}[{status}] {msg}{colors['ENDC']}")

def check_port(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((host, port))
            return True
        except:
            return False

def main():
    print_log("开始检测 No.0 AI V4.0 前端执行程序...", "INFO")
    
    # 查找 frontend 的 EXE
    frontend_exe_path = Path(__file__).parent / "frontend" / "dist_electron" / "win-unpacked" / "No.0 AI V4.0.exe"
    if not frontend_exe_path.exists():
        print_log(f"未找到前端执行程序: {frontend_exe_path}", "ERROR")
        sys.exit(1)
        
    print_log(f"找到执行程序: {frontend_exe_path}", "SUCCESS")
    print_log("正在启动主程序进行诊断...", "INFO")
    
    try:
        # 使用 Popen 启动
        process = subprocess.Popen([str(frontend_exe_path)])
        
        # 监测进程是否能在前 5 秒内稳定存活
        for i in range(5):
            if process.poll() is not None:
                print_log(f"程序启动后异常退出，退出码: {process.returncode}", "ERROR")
                sys.exit(1)
            time.sleep(1)
            
        print_log("前端 EXE 成功启动并稳定存活。", "SUCCESS")
        
        # 尝试检查后端端口 8000 是否被成功唤起
        print_log("检测后端引擎 (127.0.0.1:8000) 是否被成功唤起...", "INFO")
        backend_alive = False
        for _ in range(15):  # 给后端 15 秒启动时间
            if check_port(8000):
                backend_alive = True
                break
            time.sleep(1)
            
        if backend_alive:
            print_log("后端引擎已成功侦听端口 8000！全链路验证通过。", "SUCCESS")
        else:
            print_log("前端已启动，但后端引擎端口 8000 未响应。请检查自动唤起逻辑或查看 cmd 黑窗口错误信息。", "WARNING")
            
        print_log("测试完成。5 秒后自动关闭测试进程...", "INFO")
        time.sleep(5)
        
        # 清理进程
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
            
    except Exception as e:
        print_log(f"检测过程中发生异常: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
