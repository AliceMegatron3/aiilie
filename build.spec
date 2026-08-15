# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

# 打包为资源时的内嵌文件 (格式: (源路径, 打包内目标路径))
# 这里将所有的内置 prompts 和 config 文件打入进去。
added_files = [
    ('data/prompts/builtin', 'data/prompts/builtin'),
    ('config', 'config'),
    ('frontend/dist', 'frontend/dist')
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=added_files,
    # 添加系统经常由于动态反射或者插件系统丢失的隐藏依赖
    hiddenimports=[
        'pydantic',
        'jinja2',
        'uv',
        'sqlite3',
        'aiosqlite',
        'uvicorn',
        'fastapi',
        'starlette',
        'webview'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 如果以后引入前置的redis或不需要，强制不导入也可以在这里用 excludes 剔除不需要的巨大依赖
    excludes=[
        'tests',
        'pytest',
        'redis'  # 单机版屏蔽 redis
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='No0_AI_V4',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True, # 开启控制台以便调试
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='app.ico' # 如果有图标可以配置在这里
)
