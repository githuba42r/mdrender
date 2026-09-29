; Inno Setup script for mdrender-send.
;
; 1) Build the standalone exe:
;      python -m PyInstaller --clean --noconfirm packaging\pyinstaller\mdrender-send.spec
; 2) Compile this script with Inno Setup 6+ (ISCC.exe).
; Produces mdrender-send-<version>-setup.exe, installing to Program Files and
; optionally adding the install dir to the user PATH.

#define AppName "MDRender Send"
#define AppVersion "1.0.15"
#define ExeName "mdrender-send.exe"

[Setup]
AppId={{7C3B0B2E-6F1E-4C7A-9C2E-6D2B4B0E9A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Phil Gersekowski
DefaultDirName={autopf}\MDRender Send
DefaultGroupName=MDRender Send
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=mdrender-send-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
ChangesEnvironment=yes

[Files]
Source: "..\..\dist\{#ExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "addtopath"; Description: "Add mdrender-send to PATH"; GroupDescription: "Options:"; Flags: checkedonce

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; Tasks: addtopath; \
  Check: NeedsAddPath(ExpandConstant('{app}'))

[Code]
function NeedsAddPath(Param: string): Boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;
