/* Local-only uninstaller. Sources and photos are retained for reinstall. */
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
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
    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool GetTokenInformation(IntPtr token, int type, IntPtr buffer, int length, out int needed);
    [DllImport("advapi32.dll")]
    private static extern IntPtr GetSidSubAuthorityCount(IntPtr sid);
    [DllImport("advapi32.dll")]
    private static extern IntPtr GetSidSubAuthority(IntPtr sid, uint index);

    private static void RequireDesktopPermissions()
    {
        using (WindowsIdentity identity = WindowsIdentity.GetCurrent()) {
            int needed;
            GetTokenInformation(identity.Token, 25, IntPtr.Zero, 0, out needed);
            IntPtr buffer = Marshal.AllocHGlobal(needed);
            try {
                if (!GetTokenInformation(identity.Token, 25, buffer, needed, out needed))
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                IntPtr sid = Marshal.ReadIntPtr(buffer);
                uint index = (uint)(Marshal.ReadByte(GetSidSubAuthorityCount(sid)) - 1);
                if (Marshal.ReadInt32(GetSidSubAuthority(sid, index)) < 0x2000)
                    throw new UnauthorizedAccessException(
                        "当前卸载器运行在 Windows 低完整性级别，无法移除普通桌面程序。\r\n" +
                        "请从资源管理器运行 first_run.bat 修复安装，再运行新生成的卸载器。");
            } finally { Marshal.FreeHGlobal(buffer); }
        }
    }

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

    private static void WaitForOwnRuntime(string root)
    {
        string runtime = Path.Combine(root, @".venv\Scripts\PetPal.exe");
        string launcher = Path.Combine(root, "PetPal.exe");
        Stopwatch timer = Stopwatch.StartNew();
        while (true) {
            bool running = false;
            foreach (Process process in Process.GetProcessesByName("PetPal")) {
                using (process) {
                    string path = ImagePath(process.Id);
                    if (String.Equals(path, runtime, StringComparison.OrdinalIgnoreCase) ||
                        String.Equals(path, launcher, StringComparison.OrdinalIgnoreCase))
                        running = true;
                }
            }
            if (!running) return;
            if (timer.ElapsedMilliseconds >= 3000)
                throw new IOException("此目录的 PetPal 仍在运行。请从托盘或设置选择“退出程序”，然后重试卸载。");
            Thread.Sleep(150);
        }
    }

    private static void CheckTree(string path)
    {
        FileAttributes attributes;
        try { attributes = File.GetAttributes(path); }
        catch (FileNotFoundException) { return; }
        catch (DirectoryNotFoundException) { return; }
        if ((attributes & FileAttributes.ReparsePoint) != 0)
            throw new IOException("拒绝递归删除链接目录：" + path);
        if ((attributes & FileAttributes.Directory) == 0)
            throw new IOException("预期为目录，但发现了文件：" + path);
        foreach (string child in Directory.GetDirectories(path)) CheckTree(child);
    }

    private static void RemoveWithRetry(string path, Action remove)
    {
        Stopwatch timer = Stopwatch.StartNew();
        while (true) {
            try {
                remove();
                return;
            } catch (Exception error) {
                if (!(error is IOException || error is UnauthorizedAccessException)) throw;
                int code = error.HResult & 0xffff;
                if (code != 5 && code != 32 && code != 33) throw;
                if (timer.ElapsedMilliseconds >= 2500)
                    throw new IOException("无法删除 " + path + "（Windows " + code + "）。\r\n" +
                        "请确认该文件或目录未被占用且当前账户有删除权限，然后重试。", error);
                Thread.Sleep(150);
            }
        }
    }

    private static string Literal(string text) { return "'" + text.Replace("'", "''") + "'"; }

    private static void ScheduleSelfRemoval(string root)
    {
        string self = Path.Combine(root, "uninstall.exe");
        int pid = Process.GetCurrentProcess().Id;
        long lastWrite = File.GetLastWriteTimeUtc(self).Ticks;
        string script = "$ErrorActionPreference='Stop'; Wait-Process -Id " + pid +
            " -ErrorAction SilentlyContinue; $target=" + Literal(self) + "; " +
            "for ($i=0; $i -lt 20; $i++) { " +
            "try { if ([IO.File]::GetLastWriteTimeUtc($target).Ticks -ne " + lastWrite +
            ") { exit 0 }; [IO.File]::Delete($target); exit 0 } catch { Start-Sleep -Milliseconds 150 } }; exit 1";
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
            RequireDesktopPermissions();
            if ((File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("请从真实的软件目录运行卸载，不支持目录链接。");
            if (!silent && MessageBoxW(IntPtr.Zero,
                "卸载此目录的 PetPal？\r\n\r\n" + root +
                "\r\n\r\n将删除本地虚拟环境、缓存、两个 exe" + (keep ? "，保留 config 配置。" : "和 config 配置（含密码）。") +
                "\r\n源码与 photos 图片库会保留，方便重新安装。", "PetPal 卸载", 0x40024) != 6) return 0;
            WaitForOwnRuntime(root);
            string[] folders = keep ? new string[] { ".venv", "cache" }
                                    : new string[] { ".venv", "cache", "config" };
            foreach (string name in folders) CheckTree(Path.Combine(root, name));
            string launcher = Path.Combine(root, "PetPal.exe");
            RemoveWithRetry(launcher, () => File.Delete(launcher));
            foreach (string name in folders) {
                string folder = Path.Combine(root, name);
                RemoveWithRetry(folder, () => {
                    try { Directory.Delete(folder, true); }
                    catch (DirectoryNotFoundException) { }
                });
            }
            ScheduleSelfRemoval(root);
            if (!silent) MessageBoxW(IntPtr.Zero,
                "卸载完成。关闭提示后将清理卸载器。\r\n源码与图片库已保留。" +
                (keep ? "\r\n配置已保留。" : ""), "PetPal", 0x40040);
            return 0;
        } catch (Exception error) {
            if (!silent) MessageBoxW(IntPtr.Zero, "卸载未完成：\r\n" + error.Message, "PetPal", 0x40010);
            return 1;
        }
    }
}
