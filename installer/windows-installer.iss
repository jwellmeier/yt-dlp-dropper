; Inno Setup script placeholder for yt-dlp Dropper installer
; Use Inno Setup Compiler (ISCC.exe) to build a Windows installer.

[Setup]
AppName=yt-dlp Dropper
AppVersion=0.1.0
DefaultDirName={pf}\yt-dlp Dropper
DisableProgramGroupPage=yes
OutputBaseFilename=yt-dlp-dropper-installer

[Files]
; Add compiled frontend and launcher files here.
; Source: dist\ytdlp-dropper.exe; DestDir: {app}

[Icons]
Name: "{group}\yt-dlp Dropper"; Filename: "{app}\ytdlp-dropper.exe"
