#!/bin/bash
set -euo pipefail
TASK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$TASK_ROOT"
if [ "$#" -gt 1 ]; then
    printf 'Usage: %s [destination.app]\n' "$0" >&2
    exit 2
fi
TASK_DESTINATION="${1:-$HOME/Applications/Img2UltraHDR.app}"
swift build --package-path macos -c release --build-system native -debug-info-format none
TASK_STAGING="$(mktemp -d "$TASK_ROOT/macos/.build/app-bundle.XXXXXX")"
trap 'rm -rf "$TASK_STAGING"' EXIT
TASK_APP="$TASK_STAGING/Img2UltraHDR.app"
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
version=json.loads((root/'macos/Sources/Img2UltraHDR/Resources/AppVersion.json').read_text())
config={'root':str(root),'python':str(root/'.venv/bin/python'),'tools':{
'HDRIMG_TOOL_RAWTHERAPEE_CLI':str(tools.rawtherapee),'HDRIMG_TOOL_ULTRAHDR_APP':str(tools.ultrahdr),'HDRIMG_TOOL_EXIFTOOL':str(tools.exiftool)}}
(app/'Contents/Resources/engine.json').write_text(json.dumps(config,indent=2))
info={'CFBundleName':'Img2UltraHDR','CFBundleDisplayName':'Img2UltraHDR','CFBundleIdentifier':'local.img2ultrahdr.editor',
'CFBundleExecutable':'Img2UltraHDR','CFBundlePackageType':'APPL','CFBundleShortVersionString':version['version'],'CFBundleVersion':version['build'],
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
.venv/bin/python - "$TASK_APP" "$TASK_DESTINATION" <<'PY'
import os,plistlib,shutil,subprocess,sys,tempfile
from pathlib import Path
source=Path(sys.argv[1]);destination=Path(sys.argv[2]).expanduser().absolute()
if destination.suffix != '.app' or destination.is_symlink():
    raise SystemExit('Choose a real .app destination, not a symbolic link.')
if destination.exists():
    info=destination/'Contents/Info.plist'
    if not info.is_file() or plistlib.loads(info.read_bytes()).get('CFBundleIdentifier') != 'local.img2ultrahdr.editor':
        raise SystemExit('The destination belongs to another application; refusing to replace it.')
executable=str(destination/'Contents/MacOS/Img2UltraHDR')
commands=subprocess.check_output(['ps','-axo','command='],text=True).splitlines()
if any(line.strip()==executable or line.strip().startswith(executable+' ') for line in commands):
    raise SystemExit('Quit Img2UltraHDR before updating it, then run this script again.')
destination.parent.mkdir(parents=True,exist_ok=True)
# Stage on the destination filesystem, so replacement is a rename. The
# previous bundle is restored on failure and removed after a successful swap.
with tempfile.TemporaryDirectory(prefix='.img2ultrahdr-install-',dir=destination.parent) as temp:
    fresh=Path(temp)/'new.app';previous=Path(temp)/'previous.app'
    subprocess.run(['ditto',str(source),str(fresh)],check=True)
    subprocess.run(['codesign','--verify','--deep','--strict',str(fresh)],check=True)
    if destination.exists(): os.replace(destination,previous)
    try: os.replace(fresh,destination)
    except BaseException:
        if previous.exists(): os.replace(previous,destination)
        raise
subprocess.run(['/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister','-f',str(destination)],check=True)
print(destination)
PY
