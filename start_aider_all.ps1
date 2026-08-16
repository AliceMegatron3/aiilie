$pyFiles = @("main.py","gui.py")
$targetDirs = @("core","services","plugins","utils","src","config")

foreach($dir in $targetDirs){
    if(Test-Path $dir){
        $files = Get-ChildItem $dir -Recurse -Include *.py,*.toml,*.yaml | Select-Object -ExpandProperty FullName
        $pyFiles += $files
    }
}

$mdRefFiles = Get-ChildItem . -Depth 0 -Filter *.md | Where-Object {
    $_.Name -notmatch '\.log|\.err|backup'
} | Select-Object -ExpandProperty FullName

$allInput = $pyFiles + $mdRefFiles

if ([string]::IsNullOrWhiteSpace($env:AIILIE_AIDER_API_KEY)) {
    Write-Warning "AIILIE_AIDER_API_KEY environment variable is not set"
}

aider --no-git --model openai/gpt-5.6-luna @allInput