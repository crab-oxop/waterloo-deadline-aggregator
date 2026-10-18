"""Windows shortcuts (Desktop + Start menu) that launch the desktop app with no console window,
plus the app's identity on the taskbar: its icon (designed on the "Waterloo Deadlines UI" canvas,
App icon artboard; sources in assets/icon/) and an AppUserModelID shared by the shortcut and the
running window — so Windows shows "Waterloo Deadlines" with its own icon, not pythonw."""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "assets" / "app.ico"
ICON_SOURCES = ROOT / "assets" / "icon"
NAME = "Waterloo Deadlines"
APP_ID = "UWaterloo.WaterlooDeadlines.App"  # AppUserModelID: groups window + shortcut on the taskbar

# Each Windows icon size uses the variant drawn for it: rings drop below 64 px, the page below 32.
VARIANT_FOR_SIZE = {256: "large", 128: "large", 64: "large", 48: "medium", 40: "small", 32: "small",
                    24: "small", 20: "tiny", 16: "tiny"}


def make_icon(path: Path = ICON) -> Path:
    """Render assets/icon/*.svg at every size (headless Chromium, so it matches the design exactly)
    and pack them into one .ico."""
    from PIL import Image
    from playwright.sync_api import sync_playwright

    frames: dict[int, Image.Image] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for size, variant in VARIANT_FOR_SIZE.items():
            svg = (ICON_SOURCES / f"{variant}.svg").read_text(encoding="utf-8")
            page.set_viewport_size({"width": size, "height": size})
            page.set_content(f'<html><body style="margin:0;background:transparent">'
                             f'<div style="width:{size}px;height:{size}px">{svg.replace("<svg ", f"<svg width={size} height={size} ", 1)}</div>'
                             f'</body></html>')
            png = page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})
            frames[size] = Image.open(io.BytesIO(png)).convert("RGBA")
        browser.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    biggest = frames[max(frames)]
    others = [frames[s] for s in sorted(frames) if s != max(frames)]
    biggest.save(path, format="ICO", sizes=[(s, s) for s in sorted(frames)], append_images=others)
    return path


def set_process_identity() -> None:
    """Call before the window opens: the taskbar then groups it under APP_ID, not python.exe."""
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except OSError:
            pass


def _pythonw() -> Path:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.exists() else exe


# Stamps the shortcut with the same AppUserModelID (PKEY_AppUserModel_ID) the window uses.
_AUMID_CS = r"""
using System; using System.Runtime.InteropServices;
public static class Aumid {
  [StructLayout(LayoutKind.Sequential, Pack = 4)] public struct PropKey { public Guid fmtid; public uint pid; }
  [StructLayout(LayoutKind.Explicit)] public struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; [FieldOffset(16)] public IntPtr pad; }
  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPropertyStore { void GetCount(out uint c); void GetAt(uint i, out PropKey k); void GetValue(ref PropKey k, out PropVariant v);
    void SetValue(ref PropKey k, ref PropVariant v); void Commit(); }
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHGetPropertyStoreFromParsingName(string path, IntPtr bc, int flags, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out IPropertyStore store);
  public static void Set(string lnk, string id) {
    Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"); IPropertyStore store;
    SHGetPropertyStoreFromParsingName(lnk, IntPtr.Zero, 2, ref iid, out store);
    PropKey key = new PropKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
    PropVariant v = new PropVariant { vt = 31, p = Marshal.StringToCoTaskMemUni(id) };
    try { store.SetValue(ref key, ref v); store.Commit(); } finally { Marshal.FreeCoTaskMem(v.p); Marshal.ReleaseComObject(store); }
  }
}
"""


def install() -> list[Path]:
    icon = make_icon() if not ICON.exists() else ICON
    script = f"""
Add-Type -TypeDefinition @'
{_AUMID_CS}
'@
$ws = New-Object -ComObject WScript.Shell
$targets = @([Environment]::GetFolderPath('Desktop'), (Join-Path ([Environment]::GetFolderPath('Programs')) ''))
foreach ($dir in $targets) {{
  $path = Join-Path $dir '{NAME}.lnk'
  $lnk = $ws.CreateShortcut($path)
  $lnk.TargetPath = '{_pythonw()}'
  $lnk.Arguments = '-m aggregator app'
  $lnk.WorkingDirectory = '{ROOT}'
  $lnk.IconLocation = '{icon},0'
  $lnk.Description = 'Your Waterloo deadlines, classes and reminders'
  $lnk.Save()
  [Aumid]::Set($path, '{APP_ID}')
  Write-Output $path
}}
"""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, check=True)
    return [Path(line) for line in out.stdout.splitlines() if line.strip()]
