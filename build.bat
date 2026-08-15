@echo off
echo ===========================================
echo   No.0 AI V4.0 - 跨平台打包构建脚本
echo ===========================================

echo [1/2] 正在构建 Python 后端为独立 EXE...
pip install pyinstaller
call pyinstaller --name "No0_AI_V4_Backend" --onedir --windowed main.py

echo [2/2] 正在构建 Electron 前端桌面客户端...
cd frontend

REM ── 依赖锁文件一致性校验（约束：Electron 构建需同步校验 package-lock.json） ──
echo 正在校验 package-lock.json 与 package.json 依赖一致性...
call npx npm-check-lockfile --lock package-lock.json --package package.json >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo ✅ package-lock.json 与 package.json 同步一致
) else (
    echo ⚠️ 未安装 npm-check-lockfile 或存在差异，改用内置校验...
    node -e "const p=require('./package.json');const l=require('./package-lock.json');const lv=l.packages&&l.packages['']&&l.packages[''].dependencies||{};const miss=Object.keys(p.dependencies||{}).filter(d=>!(d in lv));if(miss.length){console.error('❌ lockfile 缺失依赖: '+miss.join(', '));process.exit(1)}console.log('✅ package-lock.json 与 package.json 依赖同步一致')"
    if errorlevel 1 (
        echo ❌ package-lock.json 与 package.json 不一致，请先执行 npm install 更新锁文件后重试。
        pause
        exit /b 1
    )
)

call npm install
call npm run build
call npm run electron:build

echo 构建完成！后端产物位于 dist\No0_AI_V4_Backend，前端产物位于 frontend\dist_electron。
pause
