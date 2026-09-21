// Compile as Windows GUI subsystem, not Console. No console is allocated at
// launch, including when Windows Terminal is the default terminal application.
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
using System.Management.Automation;
using System.Management.Automation.Runspaces;
using System.Threading;

internal static class PocketLauncher
{
    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            if (args.Length != 1 || !Path.IsPathRooted(args[0]) ||
                args[0].IndexOf('"') >= 0 ||
                !String.Equals(Path.GetFileName(args[0]), "windows-pocket-tray.ps1", StringComparison.OrdinalIgnoreCase) ||
                !File.Exists(args[0]))
                throw new InvalidOperationException("Pocket source is missing. Run Start Pocket.cmd again from the current source folder to repair the shortcut.");

            // Host the engine, NOT powershell.exe/ConsoleHost. The latter can
            // allocate a console later even with CREATE_NO_WINDOW and pipes.
            var initial = InitialSessionState.CreateDefault();
            initial.ExecutionPolicy = Microsoft.PowerShell.ExecutionPolicy.RemoteSigned;
            // Session-only policy, equivalent to the old -ExecutionPolicy flag.
            // Organization MachinePolicy/UserPolicy still take precedence.
            using (var runspace = RunspaceFactory.CreateRunspace(initial))
            using (var shell = PowerShell.Create())
            {
                runspace.ApartmentState = ApartmentState.STA;
                runspace.ThreadOptions = PSThreadOptions.UseCurrentThread;
                runspace.Open();
                Runspace.DefaultRunspace = runspace;
                shell.Runspace = runspace;
                shell.AddCommand(args[0]);
                shell.Invoke();
                return 0;
            }
        }
        catch (Exception)
        {
            MessageBox.Show("Pocket could not start. Check the source folder and run Start Pocket.cmd to repair the launcher. Windows script/security policies are still respected.",
                "Codex Pocket", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}
