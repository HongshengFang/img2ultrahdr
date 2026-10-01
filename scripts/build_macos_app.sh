#!/bin/bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$TASK_ROOT"
swift build --package-path macos -c release --build-system native -debug-info-format none
TASK_APP="$TASK_ROOT/dist/Img2UltraHDR.app"
mkdir -p "$TASK_APP/Contents/MacOS" "$TASK_APP/Contents/Resources"
cp macos/.build/release/Img2UltraHDR "$TASK_APP/Contents/MacOS/Img2UltraHDR"
cp macos/Resources-help.html "$TASK_APP/Contents/Resources/help.html"
cp macos/Resources-help-en.html "$TASK_APP/Contents/Resources/help-en.html"
cp -R macos/.build/release/Img2UltraHDR_Img2UltraHDR.bundle "$TASK_APP/Contents/Resources/"
clang -arch arm64 -mmacosx-version-min=15.0 -fblocks -O3 -ffp-contract=off -dynamiclib native/editor_accelerator.c -o "$TASK_APP/Contents/Resources/libhdreditor.dylib"
swiftc -target arm64-apple-macosx15.0 -O src/hdrimg/profiles/phone-subject.swift -o "$TASK_APP/Contents/Resources/phone-subject"
.venv/bin/python - "$TASK_APP" <<'PY'
import json,plistlib,sys
from pathlib import Path
from hdrimg.tools import resolve_tools
root=Path.cwd();app=Path(sys.argv[1]);tools=resolve_tools()
config={'root':str(root),'python':str(root/'.venv/bin/python'),'tools':{
'HDRIMG_TOOL_RAWTHERAPEE_CLI':str(tools.rawtherapee),'HDRIMG_TOOL_ULTRAHDR_APP':str(tools.ultrahdr),'HDRIMG_TOOL_EXIFTOOL':str(tools.exiftool)}}
(app/'Contents/Resources/engine.json').write_text(json.dumps(config,indent=2))
info={'CFBundleName':'Img2UltraHDR','CFBundleDisplayName':'Img2UltraHDR','CFBundleIdentifier':'local.img2ultrahdr.editor',
'CFBundleExecutable':'Img2UltraHDR','CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.2.0','CFBundleVersion':'2',
'CFBundleDevelopmentRegion':'zh-Hans','CFBundleLocalizations':['zh-Hans','en'],
'LSMinimumSystemVersion':'15.0','NSHighResolutionCapable':True,
'NSDocumentsFolderUsageDescription':'读取本机图像引擎和您选择的 RAW 原片，以生成和导出照片。',
'CFBundleDocumentTypes':[{'CFBundleTypeName':'Camera RAW','CFBundleTypeRole':'Viewer','LSHandlerRank':'Alternate','CFBundleTypeExtensions':['cr2','raf']}]}
(app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
PY
codesign --force --sign - "$TASK_APP/Contents/Resources/libhdreditor.dylib"
codesign --force --sign - "$TASK_APP/Contents/Resources/phone-subject"
codesign --force --sign - "$TASK_APP"
codesign --verify --deep --strict "$TASK_APP"
printf '%s\n' "$TASK_APP"
