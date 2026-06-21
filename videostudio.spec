# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：把 app.py 及 web/ 界面打成单文件可执行程序。
# 用法：pyinstaller --noconfirm --clean videostudio.spec
from PyInstaller.utils.hooks import collect_all

datas = [('web', 'web'), ('config.example.json', '.'),
         ('assets/icon.png', 'assets'), ('assets/icon.ico', 'assets')]
binaries = []
hiddenimports = []

# 收集 pywebview 及其原生后端（Windows 用 WebView2 / macOS 用 WebKit）
for pkg in ('webview', 'clr_loader', 'pythonnet'):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 采用 onedir（文件夹）方式：原生 DLL 以真实文件存在，
# pywebview 的 WebView2/.NET 后端才能稳定加载（onefile 常因临时解压而加载失败）。
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AutoVideoStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # 关闭 UPX，避免杀软误报
    console=False,        # 无控制台窗口；启动失败会弹窗并写 startup_error.log
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='AutoVideoStudio',
)
