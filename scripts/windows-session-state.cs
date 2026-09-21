// Read-only lock detection for Windows 11. No unlock/session-switch operation.
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

public static class PocketWindowsSession {
    // WTSINFOEXW / WTSINFOEX_LEVEL1_W from wtsapi32.h. Full layout preserves
    // native 8-byte alignment of the LARGE_INTEGER fields and enclosing union.
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    private struct Level1 {
        public uint SessionId;
        public int SessionState, SessionFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=33)] public string Station;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=21)] public string User;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst=18)] public string Domain;
        public long Logon, Connect, Disconnect, LastInput, Current;
        public uint InBytes, OutBytes, InFrames, OutFrames, InCompressed, OutCompressed;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct InfoEx { public uint Level; public Level1 Data; }
    [DllImport("wtsapi32.dll", CharSet=CharSet.Unicode, ExactSpelling=true)]
    private static extern bool WTSQuerySessionInformationW(IntPtr server, uint session,
        int infoClass, out IntPtr buffer, out uint bytes);
    [DllImport("wtsapi32.dll")] private static extern void WTSFreeMemory(IntPtr memory);
    [DllImport("kernel32.dll")] private static extern uint WTSGetActiveConsoleSessionId();
    [DllImport("user32.dll")] private static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)]
    private static extern bool GetUserObjectInformation(IntPtr obj, int index, StringBuilder name, int length, out int needed);
    [DllImport("user32.dll")] private static extern bool CloseDesktop(IntPtr desktop);

    public static bool AllowsInput(bool queried, uint level, uint actual, uint expected, int state, int flags) {
        // Active (0), explicitly unlocked (1). Unknown/disconnected/refused
        // queries fail closed. The old Windows 7 reversed flags are unsupported.
        return queried && level == 1 && expected != 0 && actual == expected && state == 0 && flags == 1;
    }
    public static bool SessionIsUnlocked() {
        uint session = (uint)Process.GetCurrentProcess().SessionId;
        if (WTSGetActiveConsoleSessionId() != session) return false;
        IntPtr buffer = IntPtr.Zero;
        try {
            uint bytes;
            bool ok = WTSQuerySessionInformationW(IntPtr.Zero, session, 25, out buffer, out bytes);
            if (!ok || buffer == IntPtr.Zero || bytes < Marshal.SizeOf(typeof(InfoEx))) return false;
            InfoEx info = (InfoEx)Marshal.PtrToStructure(buffer, typeof(InfoEx));
            return AllowsInput(true, info.Level, info.Data.SessionId, session,
                info.Data.SessionState, info.Data.SessionFlags);
        } catch { return false; }
        finally { if (buffer != IntPtr.Zero) WTSFreeMemory(buffer); }
    }
    public static bool IsUnlocked() {
        // OpenInputDesktop alone returned "Default" from an interactive worker
        // while this Windows 11 machine was locked. Require WTS evidence too.
        if (!SessionIsUnlocked()) return false;
        IntPtr desktop = OpenInputDesktop(0, false, 1);
        if (desktop == IntPtr.Zero) return false;
        try {
            var name = new StringBuilder(256); int needed;
            return GetUserObjectInformation(desktop, 2, name, 512, out needed) && name.ToString() == "Default";
        } finally { CloseDesktop(desktop); }
    }
}
