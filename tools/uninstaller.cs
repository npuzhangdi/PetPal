/* Local-only uninstaller. Sources and photos are retained for reinstall. */
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

internal static class PetPalUninstaller
{
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr window, string text, string title, uint flags);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool QueryFullProcessImageNameW(IntPtr handle, uint flags, StringBuilder path, ref uint length);
    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    private static string ImagePath(int pid)
    {
        IntPtr handle = OpenProcess(0x1000, false, pid);
        if (handle == IntPtr.Zero) return null;
        try {
            uint length = 32768;
            StringBuilder buffer = new StringBuilder((int)length);
            return QueryFullProcessImageNameW(handle, 0, buffer, ref length) ? buffer.ToString() : null;
        } finally { CloseHandle(handle); }
    }

    private static void StopOwnRuntime(string root)
    {
        string runtime = Path.Combine(root, @".venv\Scripts\PetPal.exe");
        string launcher = Path.Combine(root, "PetPal.exe");
        foreach (Process process in Process.GetProcessesByName("PetPal")) {
            using (process) {
                string path = ImagePath(process.Id);
                if (String.Equals(path, runtime, StringComparison.OrdinalIgnoreCase) ||
                    String.Equals(path, launcher, StringComparison.OrdinalIgnoreCase)) {
                    process.Kill();
                    if (!process.WaitForExit(10000)) throw new IOException("PetPal 仍在运行，请从托盘退出后重试。");
                }
            }
        }
    }

    private static void CheckTree(string path)
    {
        if (!Directory.Exists(path)) return;
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("拒绝递归删除链接目录：" + path);
        foreach (string child in Directory.GetDirectories(path)) CheckTree(child);
    }

    private static void RemoveLocalDirectory(string root, string name)
    {
        string path = Path.GetFullPath(Path.Combine(root, name));
        if (!String.Equals(Path.GetDirectoryName(path), root, StringComparison.OrdinalIgnoreCase))
            throw new IOException("目标超出软件目录。");
        CheckTree(path);
        if (Directory.Exists(path)) Directory.Delete(path, true);
    }

    private static string Literal(string text) { return "'" + text.Replace("'", "''") + "'"; }

    private static void ScheduleSelfRemoval(string root)
    {
        string self = Path.Combine(root, "uninstall.exe");
        int pid = Process.GetCurrentProcess().Id;
        string script = "$ErrorActionPreference='Stop'; Wait-Process -Id " + pid +
            " -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 300; " +
            "Remove-Item -LiteralPath " + Literal(self) + " -Force -ErrorAction SilentlyContinue";
        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
        info.Arguments = "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand " + Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.WindowStyle = ProcessWindowStyle.Hidden;
        using (Process helper = Process.Start(info)) {
            if (helper == null) throw new IOException("无法启动卸载器清理进程。");
        }
    }

    [STAThread]
    private static int Main(string[] args)
    {
        bool silent = Array.IndexOf(args, "--silent") >= 0;
        bool keep = Array.IndexOf(args, "--keep-user-data") >= 0;
        string root = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory).TrimEnd(Path.DirectorySeparatorChar);
        try {
            if (!File.Exists(Path.Combine(root, "main.py")) || !Directory.Exists(Path.Combine(root, "tools")))
                throw new IOException("请从完整的 PetPal 软件目录运行卸载程序。");
            if ((File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("请从真实的软件目录运行卸载，不支持目录链接。");
            if (!silent && MessageBoxW(IntPtr.Zero,
                "卸载此目录的 PetPal？\r\n\r\n" + root +
                "\r\n\r\n将删除本地虚拟环境、缓存、两个 exe" + (keep ? "，保留 config 配置。" : "和 config 配置（含密码）。") +
                "\r\n源码与 photos 图片库会保留，方便重新安装。", "PetPal 卸载", 0x40024) != 6) return 0;
            StopOwnRuntime(root);
            RemoveLocalDirectory(root, ".venv");
            RemoveLocalDirectory(root, "cache");
            if (!keep) RemoveLocalDirectory(root, "config");
            string launcher = Path.Combine(root, "PetPal.exe");
            if (File.Exists(launcher)) File.Delete(launcher);
            ScheduleSelfRemoval(root);
            if (!silent) MessageBoxW(IntPtr.Zero, "环境与配置已卸载。关闭提示后卸载器自动删除。\r\n源码与图片库已保留。", "PetPal", 0x40040);
            return 0;
        } catch (Exception error) {
            if (!silent) MessageBoxW(IntPtr.Zero, "卸载未完成：\r\n" + error.Message + "\r\n请关闭 PetPal 后重新运行。", "PetPal", 0x40010);
            return 1;
        }
    }
}
