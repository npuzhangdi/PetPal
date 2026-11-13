/* PetPal.exe —— 启动项（C# 5，由 Windows 自带的 csc.exe 编译，无终端框）
 *
 * 职责
 * ----
 * 1. 定位软件根目录（本 exe 所在目录）。
 * 2. 启动本目录下 .venv\Scripts\PetPal.exe（真正的 Python GUI 解释器副本）
 *    去执行 main.py。
 *
 * 为什么绕一层
 * ------------
 * 主目录的 PetPal.exe 需要自己的图标与版本信息，而真正常驻的 Python 进程必须
 * 也叫 PetPal.exe（否则任务管理器会显示 pythonw.exe）。因此：
 *   · 主目录 PetPal.exe = 本启动器，负责界面观感（图标 / 描述 / 无终端框）
 *   · .venv\Scripts\PetPal.exe = 真正的 Python 进程，名字同样是 PetPal.exe
 * 本进程在短暂启动检查后退出，任务管理器里最终只剩一个 PetPal.exe。
 *
 * 编译（不使用 C 语言，也不需要任何第三方工具链）：
 *   csc.exe /target:winexe /platform:anycpu /optimize+ ^
 *           /win32icon:assets\petpal.ico /out:PetPal.exe tools\launcher.cs
 */

using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

internal static class PetPalLauncher
{
    // ---- Win32：只借用 MessageBox，避免依赖 WinForms ----
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr hWnd, string lpText, string lpCaption, uint uType);

    private const uint MB_OK = 0x00000000;
    private const uint MB_ICONERROR = 0x00000010;
    private const uint MB_ICONWARNING = 0x00000030;
    private const uint MB_TOPMOST = 0x00040000;

    /// <summary>等子进程这么久，用于判断"是不是刚起来就崩了"。</summary>
    private const int StartupProbeMilliseconds = 2000;

    private static void ShowMessage(string text, uint icon)
    {
        MessageBoxW(IntPtr.Zero, text, "PetPal", MB_OK | icon | MB_TOPMOST);
    }

    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        if (root.EndsWith(Path.DirectorySeparatorChar.ToString()))
        {
            root = root.Substring(0, root.Length - 1);
        }

        string interpreter = Path.Combine(root, Path.Combine(".venv",
            Path.Combine("Scripts", "PetPal.exe")));
        bool checking = args.Length == 1 && args[0] == "--check-runtime";
        string entry = checking ? Path.Combine(root, @"tools\runtime_probe.py") : Path.Combine(root, "main.py");

        if (!File.Exists(interpreter))
        {
            ShowMessage(
                "PetPal 尚未安装（缺少 .venv）。\r\n\r\n"
                + "请先运行 first_run.bat 完成安装。",
                MB_ICONWARNING);
            return 1;
        }

        if (!File.Exists(entry))
        {
            ShowMessage(
                "main.py 缺失。\r\n\r\n"
                + "请确认 PetPal 文件夹完整，或重新运行 first_run.bat。",
                MB_ICONERROR);
            return 1;
        }

        ProcessStartInfo startInfo = new ProcessStartInfo();
        startInfo.FileName = interpreter;
        startInfo.Arguments = "-I -X utf8 -B \"" + entry + "\"";
        startInfo.WorkingDirectory = root;
        startInfo.UseShellExecute = false;
        startInfo.CreateNoWindow = true;
        startInfo.EnvironmentVariables.Remove("PYTHONHOME");
        startInfo.EnvironmentVariables.Remove("PYTHONPATH");
        startInfo.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
        startInfo.EnvironmentVariables.Remove("QT_PLUGIN_PATH");
        startInfo.EnvironmentVariables.Remove("QT_QPA_PLATFORM_PLUGIN_PATH");
        // Diagnostic probes explicitly choose their platform; desktop launches are native.
        if (!checking) startInfo.EnvironmentVariables["QT_QPA_PLATFORM"] = "windows";

        Process child;
        try
        {
            child = Process.Start(startInfo);
        }
        catch (Exception ex)
        {
            ShowMessage(
                "PetPal 启动失败：\r\n" + ex.Message + "\r\n\r\n"
                + "请重新运行 first_run.bat 修复运行环境。",
                MB_ICONERROR);
            return 1;
        }

        if (child == null)
        {
            ShowMessage(
                "PetPal 启动失败。\r\n\r\n"
                + "请重新运行 first_run.bat 修复运行环境。",
                MB_ICONERROR);
            return 1;
        }

        // Installation probes must finish writing their report before setup reads it.
        // Normal launches keep their short, nonblocking startup check below.
        if (checking)
        {
            if (!child.WaitForExit(20000))
            {
                try { child.Kill(); } catch (Exception) { }
                return 2;
            }
            return child.ExitCode;
        }

        // 若子进程在很短时间内就以非 0 退出，说明运行环境有问题（缺依赖等）。
        // 此时弹窗提示，比"双击后毫无反应"有用得多。
        // 退出码 0 视为正常（例如另一个实例已在运行时走的单实例分支）。
        try
        {
            if (child.WaitForExit(StartupProbeMilliseconds) && child.ExitCode != 0)
            {
                ShowMessage(
                    "PetPal 启动失败（退出码 " + child.ExitCode + "）。\r\n\r\n"
                    + "请重新运行 first_run.bat 修复运行环境，\r\n"
                    + "或查看 cache\\logs\\ 下的日志。",
                    MB_ICONERROR);
                return 1;
            }
        }
        catch (Exception)
        {
            // 读不到退出码就当作启动成功，不打扰用户
        }

        return 0;
    }
}
