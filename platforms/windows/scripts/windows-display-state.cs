// Read-only observer. Does not turn a display on/off or change power settings.
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public sealed class PocketDisplayState : NativeWindow, IDisposable {
    [DllImport("user32.dll")] private static extern IntPtr RegisterPowerSettingNotification(IntPtr handle, ref Guid setting, uint flags);
    [DllImport("user32.dll")] private static extern bool UnregisterPowerSettingNotification(IntPtr handle);
    private readonly Guid setting = new Guid("2B84C20E-AD23-4DDF-93DB-05FFBD7EFCA5");
    private IntPtr registration;
    private int state = -1;
    public PocketDisplayState() {
        CreateHandle(new CreateParams()); // Hidden window; no focus or activation.
        Guid guid = setting;
        registration = RegisterPowerSettingNotification(Handle, ref guid, 0);
        if (registration == IntPtr.Zero) { DestroyHandle(); throw new InvalidOperationException("display_state_unavailable"); }
    }
    protected override void WndProc(ref Message message) {
        if (message.Msg == 0x218 && message.WParam.ToInt64() == 0x8013 && message.LParam != IntPtr.Zero) {
            Guid guid = (Guid)Marshal.PtrToStructure(message.LParam, typeof(Guid));
            if (guid == setting && Marshal.ReadInt32(message.LParam, 16) == 4)
                state = Marshal.ReadInt32(message.LParam, 20);
        }
        base.WndProc(ref message);
    }
    public int Read() { Application.DoEvents(); return state; }
    public void Dispose() {
        if (registration != IntPtr.Zero) { UnregisterPowerSettingNotification(registration); registration = IntPtr.Zero; }
        DestroyHandle();
    }
}
