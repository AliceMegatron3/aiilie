@echo off
echo 正在清理旧打包目录dist、build...
rmdir /s /q dist 2>nul
rmdir /s /q build 2>nul

REM ── 依赖锁文件一致性校验（约束：构建需同步校验 package-lock.json） ──
if exist frontend\package-lock.json (
    echo 正在校验 frontend 依赖锁文件一致性...
    cd frontend
    node -e "const p=require('./package.json');const l=require('./package-lock.json');const lv=l.packages&&l.packages['']&&l.packages[''].dependencies||{};const miss=Object.keys(p.dependencies||{}).filter(d=>!(d in lv));if(miss.length){console.error('LOCKFILE_MISMATCH: '+miss.join(', '));process.exit(1)}console.log('OK: lockfile synced')"
    if errorlevel 1 (
        echo ❌ package-lock.json 与 package.json 依赖不一致，请先执行 npm install 更新锁文件后重试。
        pause
        exit /b 1
    )
    echo ✅ package-lock.json 校验通过
    cd ..
)

echo 开始执行PyInstaller打包...
python -m PyInstaller build.spec
echo 打包执行完成，产物在dist文件夹内
pause