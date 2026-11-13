# Creates Desktop and Start-menu shortcuts for both computer apps: "שירים" and "שירים מסונן".
# The filtered shortcut carries the same taskbar identity (AppUserModelID) as the filtered app's window, so pinning
# the running window keeps launching the filtered edition. Hebrew names are written with character codes so this
# file stays plain ASCII (Windows PowerShell 5.1 reads scripts in the ANSI code page).
param([string]$AppDir = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\Shirim'))

$code = @'
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("00021401-0000-0000-C000-000000000046")] class CShellLink {}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
interface IShellLinkW {
    void GetPath(IntPtr pszFile, int cch, IntPtr pfd, int fFlags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription(IntPtr pszName, int cch);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory(IntPtr pszDir, int cch);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments(IntPtr pszArgs, int cch);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out short pwHotkey);
    void SetHotkey(short wHotkey);
    void GetShowCmd(out int piShowCmd);
    void SetShowCmd(int iShowCmd);
    void GetIconLocation(IntPtr pszIconPath, int cch, out int piIcon);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, int dwReserved);
    void Resolve(IntPtr hwnd, int fFlags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("0000010b-0000-0000-C000-000000000046")]
interface IPersistFile {
    void GetClassID(out Guid pClassID);
    [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, int dwMode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, [MarshalAs(UnmanagedType.Bool)] bool fRemember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
struct PropertyKey { public Guid fmtid; public int pid; }

[StructLayout(LayoutKind.Explicit)]
struct PropVariant {
    [FieldOffset(0)] public ushort vt;
    [FieldOffset(8)] public IntPtr pointer;
}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
interface IPropertyStore {
    void GetCount(out uint cProps);
    void GetAt(uint iProp, out PropertyKey pkey);
    void GetValue(ref PropertyKey key, out PropVariant pv);
    void SetValue(ref PropertyKey key, ref PropVariant pv);
    void Commit();
}

public static class ShirimShortcut {
    public static void Create(string path, string target, string arguments, string workingDir, string icon, string description, string appId) {
        var link = (IShellLinkW)new CShellLink();
        link.SetPath(target);
        link.SetArguments(arguments ?? "");
        link.SetWorkingDirectory(workingDir);
        link.SetIconLocation(icon, 0);
        link.SetDescription(description);
        if (!string.IsNullOrEmpty(appId)) {
            var store = (IPropertyStore)link;
            var key = new PropertyKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
            var value = new PropVariant { vt = 31, pointer = Marshal.StringToCoTaskMemUni(appId) };  // VT_LPWSTR
            try { store.SetValue(ref key, ref value); store.Commit(); }
            finally { Marshal.FreeCoTaskMem(value.pointer); }
        }
        ((IPersistFile)link).Save(path, true);
    }
}
'@
Add-Type -TypeDefinition $code -Language CSharp

function Text([int[]]$codes) { -join ($codes | ForEach-Object { [char]$_ }) }
$regular = Text 0x05E9, 0x05D9, 0x05E8, 0x05D9, 0x05DD                                                  # shirim
$filtered = $regular + ' ' + (Text 0x05DE, 0x05E1, 0x05D5, 0x05E0, 0x05DF)                             # shirim mesunan

$exe = Join-Path $AppDir 'Shirim.exe'
if (-not (Test-Path $exe)) { throw "Shirim.exe not found in $AppDir" }
$filteredIcon = Join-Path $AppDir '_internal\shirim-filtered.ico'
if (-not (Test-Path $filteredIcon)) { $filteredIcon = $exe }

$folders = @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('Programs')) ''))
foreach ($folder in $folders) {
    [ShirimShortcut]::Create((Join-Path $folder "$regular.lnk"), $exe, '', $AppDir, $exe, 'Shirim', $null)
    [ShirimShortcut]::Create((Join-Path $folder "$filtered.lnk"), $exe, '--filtered', $AppDir, $filteredIcon,
        'Shirim (filtered edition)', 'Shirim.Filtered')
}
Write-Host "Shortcuts created in: $($folders -join ', ')"
