; faultycat_installer.iss
; Script for Inno Setup

[Setup]
AppId={{8C4E6F2A-9B1D-4A3E-8F2C-5D6E7F8A9B1C}
AppName=FaultyCat
AppVersion=3.0.0.5
AppPublisher=Electronic Cats
AppPublisherURL=https://github.com/ElectronicCats/faultycat
AppSupportURL=https://github.com/ElectronicCats/faultycat/issues
AppComments=Host CLI/TUI for FaultyCat v3 — EMFI / crowbar / SWD / JTAG fault injection
AppCopyright=Copyright © 2026 Electronic Cats
DefaultDirName={autopf}\FaultyCat
DefaultGroupName=FaultyCat
UninstallDisplayIcon={app}\faultycmd.exe
Compression=lzma2
SolidCompression=yes
OutputDir=..\dist
OutputBaseFilename=FaultyCat-Setup
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "addtopath"; Description: "Add FaultyCat to PATH"; GroupDescription: "Configuration:"; Flags: checkedonce

[Files]
Source: "..\dist\faultycmd\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\FaultyCat CLI"; Filename: "{cmd}"; Parameters: "/k ""{app}\faultycmd.exe"""; IconFilename: "{app}\faultycmd.exe"
Name: "{group}\FaultyCat Documentation"; Filename: "{app}\README.md"
Name: "{group}\Uninstall FaultyCat"; Filename: "{uninstallexe}"
Name: "{autodesktop}\FaultyCat CLI"; Filename: "{cmd}"; Parameters: "/k ""{app}\faultycmd.exe"""; IconFilename: "{app}\faultycmd.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\faultycmd.exe"; Parameters: "--help"; Description: "Verify installation"; Flags: postinstall runhidden

[Registry]
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_LOCAL_MACHINE,
    'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
    'Path', OrigPath)
  then begin
    Result := True;
    exit;
  end;
  Result := Pos(Param, OrigPath) = 0;
end;
