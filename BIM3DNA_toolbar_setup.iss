#define MyAppName "BIM3DNA Toolbar"
#define MyAppVersion "0.2.1"
#define MyPublisher "BIM3DNA"
#define MyPublisherURL "https://bim3dna.nl/"
#define MyExtensionName "BIM3DNA.extension"
#define MyOutputBaseFilename "BIM3DNA_Toolbar_Installer_0.2.1"
#if DirExists("qpdf")
#define HasBundledQpdf "1"
#else
#define HasBundledQpdf "0"
#endif

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyPublisher}
AppPublisherURL={#MyPublisherURL}
AppSupportURL={#MyPublisherURL}
DefaultDirName={userappdata}\pyRevit\Extensions\{#MyExtensionName}
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename={#MyOutputBaseFilename}
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
DisableProgramGroupPage=yes
SetupLogging=yes

[Files]
; Main BIM3DNA pyRevit extension
Source: "BIM3DNA.extension\*"; DestDir: "{userappdata}\pyRevit\Extensions\BIM3DNA.extension"; Excludes: "BIM3DNA.tab\QuickFix.panel\Create Print Set.pushbutton\create_print_set_pdf_config.json,Resources\RibbonArchive\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "BIM3DNA.extension\BIM3DNA.tab\QuickFix.panel\Create Print Set.pushbutton\create_print_set_pdf_config.json"; DestDir: "{userappdata}\pyRevit\Extensions\BIM3DNA.extension\BIM3DNA.tab\QuickFix.panel\Create Print Set.pushbutton"; Flags: ignoreversion onlyifdoesntexist skipifsourcedoesntexist

; AI runtime folders kept as siblings to the extension, matching current dev layout
Source: "Model_Service\*"; DestDir: "{userappdata}\pyRevit\Extensions\Model_Service"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "Openai_Server\*"; DestDir: "{userappdata}\pyRevit\Extensions\Openai_Server"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; Optional bundled Ollama installer
Source: "OllamaSetup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist

; Optional bundled portable qpdf for Create Print Set / PDF cleanup workflows
Source: "qpdf\*"; DestDir: "{userappdata}\pyRevit\Extensions\BIM3DNA.extension\_runtime\qpdf"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; --------------------------------------------------------------------------
; OPTIONAL: only use these if these folders still exist OUTSIDE the extension
; and are required at runtime on target machines.
; If you move them inside BIM3DNA.extension, remove these entries.
; --------------------------------------------------------------------------
; Source: "Model_Service\*"; DestDir: "{userappdata}\pyRevit\Extensions\{#MyExtensionName}\Model_Service"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; Source: "openai_Server\*"; DestDir: "{userappdata}\pyRevit\Extensions\{#MyExtensionName}\openai_Server"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; Source: "Openai_Server\*"; DestDir: "{userappdata}\pyRevit\Extensions\{#MyExtensionName}\Openai_Server"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; Source: "Model_Service\*.json"; DestDir: "{userappdata}\pyRevit\Extensions\{#MyExtensionName}\Model_Service"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\pyRevit\Extensions\{#MyExtensionName}"

[Code]
var
  OllamaPage: TInputOptionWizardPage;
  LocalInstallerPage: TInputQueryWizardPage;
  ModelPage: TInputOptionWizardPage;
  QpdfPage: TInputOptionWizardPage;
  QpdfBrowsePage: TInputQueryWizardPage;
  OllamaBrowseButton: TNewButton;
  QpdfBrowseButton: TNewButton;

function OllamaExePath(): string;
begin
  Result := ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe');
end;

function BundledInstallerPath(): string;
begin
  Result := ExpandConstant('{tmp}\OllamaSetup.exe');
end;

function HasBundledInstaller(): Boolean;
begin
  Result := FileExists(BundledInstallerPath());
end;

function IsOllamaInstalled(): Boolean;
begin
  Result := FileExists(OllamaExePath());
end;

function IsOllamaCliReady(): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;

  if not IsOllamaInstalled() then
    exit;

  Result := Exec(
    OllamaExePath(),
    'list',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) and (ResultCode = 0);
end;

function StartOllamaAndWait(): Boolean;
var
  ResultCode: Integer;
  I: Integer;
begin
  Result := False;

  if not IsOllamaInstalled() then
    exit;

  if IsOllamaCliReady() then
  begin
    Result := True;
    exit;
  end;

  Exec(OllamaExePath(), 'serve', '', SW_HIDE, ewNoWait, ResultCode);

  for I := 1 to 12 do
  begin
    Sleep(2000);
    if IsOllamaCliReady() then
    begin
      Result := True;
      exit;
    end;
  end;
end;

function PullOllamaModel(const ModelName: string): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(
    OllamaExePath(),
    'pull ' + ModelName,
    '',
    SW_SHOW,
    ewWaitUntilTerminated,
    ResultCode
  ) and (ResultCode = 0);
end;

function SelectedModelName(): string;
begin
  Result := '';

  { Keep this aligned with the default model configured in the BIM3DNA AI tool }
  if ModelPage.Values[0] then
    Result := 'phi3:mini'
  else if ModelPage.Values[1] then
    Result := 'phi3'
  else if ModelPage.Values[2] then
    Result := 'gemma3:1b'
  else if ModelPage.Values[3] then
    Result := 'llama3.2:1b'
  else if ModelPage.Values[4] then
    Result := 'llama3.2:3b'
  else
    Result := '';
end;

procedure BrowseLocalInstallerClick(Sender: TObject);
var
  FileName: string;
begin
  FileName := Trim(LocalInstallerPage.Values[0]);
  if FileName = '' then
    FileName := ExpandConstant('{userdesktop}\OllamaSetup.exe');

  if GetOpenFileName(
      'Select local Ollama installer',
      FileName,
      '',
      'Executable files (*.exe)|*.exe',
      'exe') then
  begin
    LocalInstallerPage.Values[0] := FileName;
  end;
end;

function InstallBundledOllama(): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;

  if not HasBundledInstaller() then
  begin
    MsgBox(
      'No bundled Ollama installer was found inside this setup package.' + #13#10 + #13#10 +
      'Place OllamaSetup.exe next to this .iss file before compiling, or choose another installation option.',
      mbError,
      MB_OK
    );
    exit;
  end;

  Result := Exec(
    BundledInstallerPath(),
    '',
    '',
    SW_SHOW,
    ewWaitUntilTerminated,
    ResultCode
  ) and (ResultCode = 0);
end;

function InstallLocalOllama(): Boolean;
var
  ResultCode: Integer;
  InstallerPath: string;
begin
  Result := False;
  InstallerPath := Trim(LocalInstallerPage.Values[0]);

  if (InstallerPath = '') or (not FileExists(InstallerPath)) then
  begin
    MsgBox(
      'The selected Ollama installer is empty or invalid.' + #13#10 + #13#10 +
      'Please browse to a valid OllamaSetup.exe file or choose another option.',
      mbError,
      MB_OK
    );
    exit;
  end;

  Result := Exec(
    InstallerPath,
    '',
    '',
    SW_SHOW,
    ewWaitUntilTerminated,
    ResultCode
  ) and (ResultCode = 0);
end;

function InstallOfficialOllamaNow(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(
    ExpandConstant('{cmd}'),
    '/C powershell -ExecutionPolicy Bypass -Command "irm https://ollama.com/install.ps1 | iex"',
    '',
    SW_SHOW,
    ewWaitUntilTerminated,
    ResultCode
  ) and (ResultCode = 0);
end;

procedure OpenOfficialOllamaDownload();
var
  ResultCode: Integer;
begin
  ShellExec(
    'open',
    'https://ollama.com/download/windows',
    '',
    '',
    SW_SHOWNORMAL,
    ewNoWait,
    ResultCode
  );
end;

function ExtensionInstallDir(): string;
begin
  Result := ExpandConstant('{userappdata}\pyRevit\Extensions\{#MyExtensionName}');
end;

function ExternalToolsConfigPath(): string;
begin
  Result := ExpandConstant('{userappdata}\BIM3DNA\external_tools.json');
end;

function BundledQpdfExePath(): string;
var
  Candidate: string;
begin
  Candidate := ExtensionInstallDir() + '\_runtime\qpdf\bin\qpdf.exe';
  if FileExists(Candidate) then
  begin
    Result := Candidate;
    exit;
  end;

  Candidate := ExtensionInstallDir() + '\_runtime\qpdf\qpdf.exe';
  if FileExists(Candidate) then
  begin
    Result := Candidate;
    exit;
  end;

  Result := '';
end;

function IsBundledQpdfAvailable(): Boolean;
begin
  Result := BundledQpdfExePath() <> '';
end;

function TestQpdfExePath(const Path: string): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  if (Path = '') or (not FileExists(Path)) then
    exit;

  Result := Exec(Path, '--version', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function FindQpdfInFolderPattern(const Root, Prefix: string): string;
var
  FindRec: TFindRec;
  Candidate: string;
begin
  Result := '';
  if not DirExists(Root) then
    exit;

  if FindFirst(Root + '\' + Prefix + '*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        begin
          Candidate := Root + '\' + FindRec.Name + '\bin\qpdf.exe';
          if TestQpdfExePath(Candidate) then
          begin
            Result := Candidate;
            exit;
          end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function DetectQpdfOnPath(): string;
var
  ResultCode: Integer;
  TempFile: string;
  Output: AnsiString;
  FirstLine: string;
  BreakPos: Integer;
begin
  Result := '';
  TempFile := ExpandConstant('{tmp}\bim3dna_where_qpdf.txt');
  if Exec(ExpandConstant('{cmd}'), '/C where qpdf > "' + TempFile + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
  begin
    if LoadStringFromFile(TempFile, Output) then
    begin
      FirstLine := Trim(String(Output));
      BreakPos := Pos(#13, FirstLine);
      if BreakPos = 0 then
        BreakPos := Pos(#10, FirstLine);
      if BreakPos > 0 then
        FirstLine := Trim(Copy(FirstLine, 1, BreakPos - 1));
      if FirstLine <> '' then
      begin
        if TestQpdfExePath(FirstLine) then
          Result := FirstLine;
      end;
    end;
  end;
end;

function DetectInstalledQpdfPath(): string;
var
  Candidate: string;
begin
  Candidate := 'C:\Program Files\qpdf\bin\qpdf.exe';
  if TestQpdfExePath(Candidate) then
  begin
    Result := Candidate;
    exit;
  end;

  Candidate := FindQpdfInFolderPattern('C:\Program Files', 'qpdf');
  if Candidate <> '' then
  begin
    Result := Candidate;
    exit;
  end;

  Candidate := 'C:\Program Files (x86)\qpdf\bin\qpdf.exe';
  if TestQpdfExePath(Candidate) then
  begin
    Result := Candidate;
    exit;
  end;

  Candidate := FindQpdfInFolderPattern('C:\Program Files (x86)', 'qpdf');
  if Candidate <> '' then
  begin
    Result := Candidate;
    exit;
  end;

  Result := DetectQpdfOnPath();
end;

function JsonEscape(const Value: string): string;
var
  S: string;
begin
  S := Value;
  StringChangeEx(S, '\', '\\', True);
  StringChangeEx(S, '"', '\"', True);
  Result := S;
end;

procedure WriteQpdfConfig(const Path: string);
var
  ConfigDir: string;
  Content: string;
begin
  if Path = '' then
    exit;

  ConfigDir := ExtractFileDir(ExternalToolsConfigPath());
  if not DirExists(ConfigDir) then
    ForceDirectories(ConfigDir);

  Content :=
    '{' + #13#10 +
    '  "qpdf_path": "' + JsonEscape(Path) + '",' + #13#10 +
    '  "qpdf_configured_by": "BIM3DNA Toolbar Installer",' + #13#10 +
    '  "qpdf_configured_at": "installer"' + #13#10 +
    '}';

  SaveStringToFile(ExternalToolsConfigPath(), Content, False);
  Log('qpdf config written to ' + ExternalToolsConfigPath() + ': ' + Path);
end;

function ReadExistingQpdfConfigPath(): string;
var
  ConfigPath: string;
  Content: AnsiString;
  Text: string;
  Marker: string;
  StartPos: Integer;
  EndPos: Integer;
begin
  Result := '';
  ConfigPath := ExternalToolsConfigPath();
  if not FileExists(ConfigPath) then
    exit;

  if not LoadStringFromFile(ConfigPath, Content) then
    exit;

  Text := String(Content);
  Marker := '"qpdf_path"';
  StartPos := Pos(Marker, Text);
  if StartPos = 0 then
    exit;
  StartPos := Pos(':', Copy(Text, StartPos, Length(Text)));
  if StartPos = 0 then
    exit;
  Text := Copy(Text, Pos(Marker, Text) + Length(Marker), Length(Text));
  StartPos := Pos('"', Text);
  if StartPos = 0 then
    exit;
  Text := Copy(Text, StartPos + 1, Length(Text));
  EndPos := Pos('"', Text);
  if EndPos = 0 then
    exit;
  Result := Copy(Text, 1, EndPos - 1);
  StringChangeEx(Result, '\\', '\', True);
end;

procedure BrowseQpdfExeClick(Sender: TObject);
var
  FileName: string;
begin
  FileName := Trim(QpdfBrowsePage.Values[0]);
  if FileName = '' then
    FileName := 'C:\Program Files\qpdf\bin\qpdf.exe';

  if GetOpenFileName(
      'Select qpdf.exe',
      FileName,
      '',
      'qpdf.exe|qpdf.exe|Executable files (*.exe)|*.exe',
      'exe') then
  begin
    QpdfBrowsePage.Values[0] := FileName;
  end;
end;

procedure ConfigureQpdf();
var
  ExistingPath: string;
  QpdfPath: string;
begin
  ExistingPath := ReadExistingQpdfConfigPath();
  if TestQpdfExePath(ExistingPath) then
  begin
    Log('Existing valid qpdf configuration preserved: ' + ExistingPath);
    exit;
  end;

  QpdfPath := '';
  if QpdfPage.Values[0] then
  begin
    QpdfPath := BundledQpdfExePath();
    if QpdfPath = '' then
      Log('Bundled qpdf was selected but no bundled qpdf executable was installed.');
  end
  else if QpdfPage.Values[1] then
  begin
    QpdfPath := DetectInstalledQpdfPath();
  end
  else if QpdfPage.Values[2] then
  begin
    QpdfPath := Trim(QpdfBrowsePage.Values[0]);
    if not TestQpdfExePath(QpdfPath) then
      QpdfPath := '';
  end
  else
  begin
    Log('qpdf setup skipped by user.');
    exit;
  end;

  if QpdfPath <> '' then
  begin
    WriteQpdfConfig(QpdfPath);
  end
  else
  begin
    MsgBox(
      'qpdf was not found. The BIM3DNA toolbar was installed, but qpdf-based compression will remain unavailable until qpdf.exe is configured.',
      mbInformation,
      MB_OK
    );
    Log('qpdf was not configured.');
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;

  if (PageID = OllamaPage.ID) and IsOllamaInstalled() then
  begin
    Result := True;
    exit;
  end;

  if (PageID = LocalInstallerPage.ID) then
  begin
    if IsOllamaInstalled() then
    begin
      Result := True;
      exit;
    end;

    if not OllamaPage.Values[1] then
    begin
      Result := True;
      exit;
    end;
  end;

  if (PageID = ModelPage.ID) then
  begin
    if IsOllamaInstalled() then
    begin
      Result := False;
      exit;
    end;

    if OllamaPage.Values[0] or OllamaPage.Values[1] or OllamaPage.Values[2] then
      Result := False
    else
      Result := True;
  end;

  if (PageID = QpdfBrowsePage.ID) then
  begin
    if not QpdfPage.Values[2] then
    begin
      Result := True;
      exit;
    end;
  end;
end;

procedure InitializeWizard;
begin
  OllamaPage :=
    CreateInputOptionPage(
      wpSelectDir,
      'Ollama Setup',
      'Local AI runtime dependency',
      'The BIM3DNA toolbar includes AI-assisted features that can use Ollama for local models.' + #13#10 + #13#10 +
      'Choose how you want to handle Ollama setup on this machine.' + #13#10 +
      'The toolbar itself will still be installed regardless.',
      True,
      False
    );

  OllamaPage.Add('Use bundled Ollama installer (recommended if included)');
  OllamaPage.Add('Browse to local Ollama installer');
  OllamaPage.Add('Install from official source now');
  OllamaPage.Add('Open official download page');
  OllamaPage.Add('Skip Ollama setup for now');

  if HasBundledInstaller() then
    OllamaPage.Values[0] := True
  else
    OllamaPage.Values[2] := True;

  LocalInstallerPage :=
    CreateInputQueryPage(
      OllamaPage.ID,
      'Local Ollama Installer',
      'Choose a local Ollama installer',
      'Provide a path to an existing OllamaSetup.exe file on this machine or a shared location.'
    );

  LocalInstallerPage.Add('Local Ollama installer path:', False);
  LocalInstallerPage.Values[0] := ExpandConstant('{userdesktop}\OllamaSetup.exe');

  OllamaBrowseButton := TNewButton.Create(LocalInstallerPage.Surface);
  OllamaBrowseButton.Parent := LocalInstallerPage.Surface;
  OllamaBrowseButton.Left := LocalInstallerPage.Edits[0].Left + LocalInstallerPage.Edits[0].Width + ScaleX(8);
  OllamaBrowseButton.Top := LocalInstallerPage.Edits[0].Top - ScaleY(1);
  OllamaBrowseButton.Width := ScaleX(75);
  OllamaBrowseButton.Height := LocalInstallerPage.Edits[0].Height + ScaleY(2);
  OllamaBrowseButton.Caption := 'Browse...';
  OllamaBrowseButton.OnClick := @BrowseLocalInstallerClick;

  ModelPage :=
    CreateInputOptionPage(
      LocalInstallerPage.ID,
      'Ollama Model Setup',
      'Choose an initial supported model',
      'Pick one pinned starter model for team testing.' + #13#10 + #13#10 +
      'Keep this aligned with the AI Workbench default model in the toolbar.',
      True,
      False
    );

  ModelPage.Add('phi3:mini (recommended for most PCs)');
  ModelPage.Add('phi3');
  ModelPage.Add('gemma3:1b');
  ModelPage.Add('llama3.2:1b');
  ModelPage.Add('llama3.2:3b');
  ModelPage.Add('Skip model download for now');
  ModelPage.Values[0] := True;

  QpdfPage :=
    CreateInputOptionPage(
      ModelPage.ID,
      'qpdf Setup',
      'PDF compression dependency',
      'The Create Print Set / PDF export tools can use qpdf for post-export PDF optimization.' + #13#10 +
      'Choose how to configure qpdf on this machine.',
      True,
      False
    );

  QpdfPage.Add('Use bundled portable qpdf if included');
  QpdfPage.Add('Auto-detect existing qpdf installation');
  QpdfPage.Add('Browse to qpdf.exe');
  QpdfPage.Add('Skip qpdf setup for now');

  if '{#HasBundledQpdf}' = '1' then
    QpdfPage.Values[0] := True
  else
    QpdfPage.Values[1] := True;

  QpdfBrowsePage :=
    CreateInputQueryPage(
      QpdfPage.ID,
      'qpdf Executable',
      'Choose qpdf.exe',
      'Browse to the installed command-line executable, usually under C:\Program Files\qpdf...\bin\qpdf.exe.'
    );

  QpdfBrowsePage.Add('qpdf.exe path:', False);
  QpdfBrowsePage.Values[0] := 'C:\Program Files\qpdf\bin\qpdf.exe';

  QpdfBrowseButton := TNewButton.Create(QpdfBrowsePage.Surface);
  QpdfBrowseButton.Parent := QpdfBrowsePage.Surface;
  QpdfBrowseButton.Left := QpdfBrowsePage.Edits[0].Left + QpdfBrowsePage.Edits[0].Width + ScaleX(8);
  QpdfBrowseButton.Top := QpdfBrowsePage.Edits[0].Top - ScaleY(1);
  QpdfBrowseButton.Width := ScaleX(75);
  QpdfBrowseButton.Height := QpdfBrowsePage.Edits[0].Height + ScaleY(2);
  QpdfBrowseButton.Caption := 'Browse...';
  QpdfBrowseButton.OnClick := @BrowseQpdfExeClick;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  InstallOk: Boolean;
  ModelName: string;
begin
  if CurStep <> ssPostInstall then
    exit;

  Log('BIM3DNA install mode: standard file-copy install.');
  Log('Git-backed Update BIM3DNA requires the installed extension to be inside a Git working tree. File-copy installs will report Git-backed update unavailable.');
  ConfigureQpdf();

  if not IsOllamaInstalled() then
  begin
    InstallOk := False;

    if OllamaPage.Values[0] then
      InstallOk := InstallBundledOllama()
    else if OllamaPage.Values[1] then
      InstallOk := InstallLocalOllama()
    else if OllamaPage.Values[2] then
      InstallOk := InstallOfficialOllamaNow()
    else if OllamaPage.Values[3] then
    begin
      OpenOfficialOllamaDownload();
      MsgBox(
        'The BIM3DNA toolbar was installed.' + #13#10 + #13#10 +
        'The official Ollama download page has been opened.' + #13#10 +
        'After installing Ollama, run this installer again if you want automated model setup.',
        mbInformation,
        MB_OK
      );
      exit;
    end
    else
    begin
      MsgBox(
        'The BIM3DNA toolbar was installed, but Ollama setup was skipped.' + #13#10 + #13#10 +
        'The AI features will not function until Ollama is installed.',
        mbInformation,
        MB_OK
      );
      exit;
    end;

    if not InstallOk then
    begin
      MsgBox(
        'The BIM3DNA toolbar was installed, but Ollama installation did not complete successfully.' + #13#10 + #13#10 +
        'You can run the installer again later or install Ollama manually.',
        mbInformation,
        MB_OK
      );
      exit;
    end;
  end;

  if not StartOllamaAndWait() then
  begin
    MsgBox(
      'Ollama appears to be installed, but it is not ready yet.' + #13#10 + #13#10 +
      'Please start Ollama manually and wait a moment before using the AI features.',
      mbInformation,
      MB_OK
    );
    exit;
  end;

  ModelName := SelectedModelName();
  if ModelName <> '' then
  begin
    if PullOllamaModel(ModelName) then
    begin
      MsgBox(
        'Ollama is ready and the selected model "' + ModelName + '" was installed successfully.' + #13#10 + #13#10 +
        'Make sure the BIM3DNA AI configuration uses the same model name.',
        mbInformation,
        MB_OK
      );
    end
    else
    begin
      MsgBox(
        'Ollama is installed, but the model download failed.' + #13#10 + #13#10 +
        'You can run this manually later:' + #13#10 +
        'ollama pull ' + ModelName,
        mbError,
        MB_OK
      );
    end;
  end
  else
  begin
    MsgBox(
      'Ollama is installed and ready. Model download was skipped.',
      mbInformation,
      MB_OK
    );
  end;

  MsgBox(
    'Installation finished.' + #13#10 + #13#10 +
    'If Revit is already open, reload pyRevit or restart Revit to load the new BIM3DNA toolbar.',
    mbInformation,
    MB_OK
  );
end;
