cd C:\Users\11482\Documents\aiilie
$env:OPENAI_API_BASE="https://api.apikey.fun/v1"
$env:OPENAI_API_KEY="sk-f391179edf99dea43c28986abfc40de2452f69f1a985f5e56d1cff8eb0edda17"

# ----------------------
# ①收集源码文件：核心业务目录，跳过虚拟环境、缓存、tests
# ----------------------
$targetDirs = @("core","services","plugins","utils","src","config")
$pyFiles = @("main.py","gui.py")
foreach($dir in $targetDirs){
    if(Test-Path $dir){
        $files = Get-ChildItem $dir -Recurse -Include *.py,*.toml,*.yaml | Select-Object -ExpandProperty FullName
        $pyFiles += $files
    }
}

# ----------------------
# ②收集根目录全部md报告（PS5.1兼容，Depth 0=仅当前目录，不进入子文件夹）
# ----------------------
$mdRefFiles = Get-ChildItem . -Depth 0 -Filter *.md | Where-Object {
    $_.Name -notmatch '\.log|\.err|backup'
} | Select-Object -ExpandProperty FullName

# 合并：源码(可修改) + md参考文档(只读)
$allInput = $pyFiles + $mdRefFiles

# 启动Aider
aider --no-git --model openai/gpt-5.6-luna @allInput