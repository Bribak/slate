// The slate.exe that Windows sees: a named program with an icon and version info for Explorer, "Open with", the
// Start menu and Settings > Default apps. It starts slate's Python and passes its arguments on unchanged.
// `python -m slate --install` fills in the placeholders and compiles it with .NET Framework's csc.exe.
using System;
using System.Diagnostics;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("slate")]
[assembly: AssemblyProduct("slate")]
[assembly: AssemblyDescription("A small Python editor")]
[assembly: AssemblyVersion("{VERSION}")]
[assembly: AssemblyFileVersion("{VERSION}")]

static class Launcher {
    const string Pythonw = @"{PYTHONW}";

    static int Main() {
        string line = Environment.CommandLine;
        int start = line.StartsWith("\"") ? line.IndexOf('"', 1) + 1 : line.IndexOf(' ');
        string rest = start <= 0 ? "" : line.Substring(start).Trim();
        ProcessStartInfo info = new ProcessStartInfo(Pythonw, "-m slate " + rest);
        info.UseShellExecute = false;
        try {
            Process.Start(info);
            return 0;
        } catch (Exception e) {
            MessageBox.Show("slate could not start " + Pythonw + ":\n" + e.Message + "\n\nReinstall with: python -m slate --install", "slate");
            return 1;
        }
    }
}
