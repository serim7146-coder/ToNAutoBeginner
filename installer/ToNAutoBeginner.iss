; ToNAutoBeginner のインストーラー（Inno Setup 6）。
; ビルドは build.py が exe の後に続けて行う（ISCC /DAppVersion=<版> installer\ToNAutoBeginner.iss）。
; 手で作るときは、リポジトリ直下に ToNAutoBeginner.exe がある状態で上と同じコマンドを打つ。
;
; - ユーザー単位で入れる（管理者権限なし。%LOCALAPPDATA%\Programs\ToNAutoBeginner）。
;   自動更新は exe の横で差し替えるので、Program Files には入れない（書き込めず更新が失敗する）
; - アップデート（自動更新の差し替え・Setup の上書き）ではアンインストールは走らない。データは残る
; - アンインストールは全部消す: インストール先・%APPDATA%\ToNAutoBeginner（設定・統計・ログ・画像）・
;   %LOCALAPPDATA%\ToNAutoBeginner（exe の展開先）
; - 起動中かは AppMutex（src/config.py の APP_MUTEX_NAME と同じ名前）で見る
; - 新しく入れる人はインストール先を選ぶ（上書きのときは前の場所のまま聞かない）。
;   管理者権限が要る場所（Program Files など）は選べない（自動更新が書き込めない）
; - exe 単体からの移行（src/Migration.py）は /VERYSILENT /launch=1 で動かす。終わったら起動する

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "ToNAutoBeginner"
#define AppExe "ToNAutoBeginner.exe"

[Setup]
; AppId は変えない（変えると別のアプリ扱いになり、上書きもアンインストールも効かなくなる）
AppId={{F3825561-B8B1-4328-8C50-51C1EFCF0BF5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher=serim7146-coder
AppPublisherURL=https://github.com/serim7146-coder/ToNAutoBeginner
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableDirPage=auto
DisableProgramGroupPage=yes
AppMutex=ToNAutoBeginnerRunning
OutputDir=..\dist
OutputBaseFilename=ToNAutoBeginner-Setup
SetupIconFile=..\ToNAutoBeginnerIcon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; exe 単体からの移行（画面なし）では、終わったらそのまま起動する
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: LaunchAfterSilentInstall

[UninstallDelete]
; 自動更新の残り（前の exe）
Type: files; Name: "{app}\{#AppExe}.old"
; 設定・統計・ログ・Begin の画像など
Type: filesandordirs; Name: "{userappdata}\{#AppName}"
; exe の展開先（onefile。版ごとのフォルダ）
Type: filesandordirs; Name: "{localappdata}\{#AppName}"
Type: dirifempty; Name: "{app}"

[Code]
function LaunchAfterSilentInstall: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:launch|0}') = '1');
end;

function IsUnder(Dir, Parent: String): Boolean;
begin
  Parent := Lowercase(Parent);
  if Parent = '' then
    Result := False
  else
    Result := Pos(AddBackslash(Parent), AddBackslash(Lowercase(Dir))) = 1;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Dir: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    Dir := WizardDirValue;
    if IsUnder(Dir, GetEnv('ProgramW6432')) or IsUnder(Dir, GetEnv('ProgramFiles'))
       or IsUnder(Dir, GetEnv('ProgramFiles(x86)')) or IsUnder(Dir, GetEnv('SystemRoot')) then
    begin
      MsgBox('Program Files など、管理者権限が要るフォルダには入れられません' + #13#10 +
             '（自動更新で書き換えられなくなります）。別のフォルダを選んでください。',
             mbError, MB_OK);
      Result := False;
    end;
  end;
end;
