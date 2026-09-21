using System;
using System.Runtime.InteropServices;

// Only the verified taskbar activator uses this; never used to submit a prompt.
public static class PocketTaskbarInput {
    [StructLayout(LayoutKind.Sequential)] public struct Point { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] struct LastInput { public uint Size, Time; }
    [StructLayout(LayoutKind.Sequential)] struct MouseInput {
        public int X, Y; public uint Data, Flags, Time; public UIntPtr Extra;
    }
    [StructLayout(LayoutKind.Sequential)] struct Input { public uint Type; public MouseInput Mouse; }
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(string cls, string title);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern IntPtr WindowFromPhysicalPoint(Point p);
    [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint flags);
    [DllImport("user32.dll")] public static extern bool GetPhysicalCursorPos(out Point p);
    [DllImport("user32.dll")] public static extern bool SetPhysicalCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LastInput info);
    [DllImport("user32.dll", SetLastError=true)] static extern uint SendInput(uint count, Input[] input, int size);
    public static bool KeysUp() {
        for (int key=1; key<255; key++) if ((GetAsyncKeyState(key) & 0x8000)!=0) return false;
        return true;
    }
    public static uint InputStamp() {
        LastInput v=new LastInput(); v.Size=(uint)Marshal.SizeOf(typeof(LastInput));
        if (!GetLastInputInfo(ref v)) throw new InvalidOperationException("taskbar_input_unavailable");
        return v.Time;
    }
    public static bool At(int x, int y) {
        Point p; return GetPhysicalCursorPos(out p) && p.X==x && p.Y==y;
    }
    public static bool OverTaskbar(int x,int y,IntPtr taskbar) {
        Point p=new Point(); p.X=x; p.Y=y;
        return GetAncestor(WindowFromPhysicalPoint(p),2)==taskbar;
    }
    public static uint Click(int x,int y,IntPtr taskbar,IntPtr codex,uint stamp) {
        // Last checks immediately before insertion, without a PowerShell/UIA wait.
        if (!At(x,y) || !KeysUp() || InputStamp()!=stamp ||
            !OverTaskbar(x,y,taskbar) || GetForegroundWindow()==codex)
            throw new InvalidOperationException("taskbar_input_changed");
        Input[] events=new Input[2];
        events[0].Mouse.Flags=0x0002; events[1].Mouse.Flags=0x0004;
        uint sent=SendInput(2,events,Marshal.SizeOf(typeof(Input)));
        // If only down was inserted, release it; never retry a click.
        if(sent==1) { Input[] release=new Input[1];release[0].Mouse.Flags=0x0004;SendInput(1,release,Marshal.SizeOf(typeof(Input))); }
        return sent;
    }
    public static int InputSize() { return Marshal.SizeOf(typeof(Input)); }
}
