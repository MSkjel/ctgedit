// ctgctl: runs an embedded control (ActiveX, Process Analyst, Scheduler ...) of a page at
// design time, in its own 32-bit process. ctgedit compiles it on first use
// (ctgedit/backend/controlhost.py) with the .NET Framework's csc.exe.
//
//   ctgctl draw <clsid> <state.stg> <picture.png> <width> <height>
//   ctgctl edit <clsid> <state.stg> <new.stg> <width> <height> [owner hwnd]
//
// state.stg is a private copy of the control's saved state (the OLE compound file of its
// record), never a project file. edit shows the control with its properties and writes
// new.stg on OK (the control's own property pages, else a list of its properties). Exit codes: 0 done, 1 failed (the reason on stderr), 2 cancelled, 3 the
// control painted nothing (no picture).
// CTG_CONTROL_BIN: the Plant SCADA Bin folder, searched for the controls' own DLLs.

using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Forms;

internal static class ControlHost
{
    [ComImport, Guid("0000010A-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IPersistStorage
    {
        void GetClassID(out Guid clsid);
        [PreserveSig] int IsDirty();
        void InitNew(IntPtr storage);
        void Load(IntPtr storage);
        void Save(IntPtr storage, [MarshalAs(UnmanagedType.Bool)] bool same);
        void SaveCompleted(IntPtr storage);
        void HandsOffStorage();
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Pages { public int Count; public IntPtr Ids; }

    [ComImport, Guid("B196B28B-BAB4-101A-B69C-00AA00341D07"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface ISpecifyPropertyPages { void GetPages(out Pages pages); }

    private const int StgmReadWrite = 0x2, StgmShareExclusive = 0x10, StgmCreate = 0x1000;

    [DllImport("ole32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    private static extern void StgCreateDocfile(string name, int mode, int reserved, out IntPtr storage);
    [DllImport("ole32.dll", PreserveSig = false)]
    private static extern void WriteClassStg(IntPtr storage, ref Guid clsid);

    private static void WriteClassStg(IntPtr storage, Guid clsid) { WriteClassStg(storage, ref clsid); }

    [DllImport("ole32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    private static extern void StgOpenStorage(string name, IntPtr priority, int mode, IntPtr exclude, int reserved, out IntPtr storage);
    [DllImport("ole32.dll", PreserveSig = false)]
    private static extern void ReadClassStg(IntPtr storage, out Guid clsid);
    [DllImport("oleaut32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    private static extern void OleCreatePropertyFrame(IntPtr owner, int x, int y, string caption, int count,
        ref IntPtr control, int pageCount, IntPtr pages, int locale, int reserved, IntPtr unused);

    // A design-time site: the control draws and behaves as in an editor, not at run time.
    private sealed class DesignContainer : System.ComponentModel.Container
    {
        protected override System.ComponentModel.ISite CreateSite(System.ComponentModel.IComponent component, string name)
        {
            return new Site(component, this, name);
        }

        private sealed class Site : System.ComponentModel.ISite
        {
            public Site(System.ComponentModel.IComponent component, System.ComponentModel.IContainer container, string name)
            {
                Component = component;
                Container = container;
                Name = name;
            }
            public bool DesignMode { get { return true; } }
            public System.ComponentModel.IComponent Component { get; private set; }
            public System.ComponentModel.IContainer Container { get; private set; }
            public string Name { get; set; }
            public object GetService(Type type) { return null; }
        }
    }

    private sealed class Host : AxHost
    {
        public Host(Guid id) : base(id.ToString("B")) { }
        public object Instance { get { return GetOcx(); } }
    }

    private sealed class Owner : IWin32Window
    {
        public Owner(long handle) { Handle = new IntPtr(handle); }
        public IntPtr Handle { get; private set; }
    }

    // A control without property pages: the control with a list of its properties.
    private static bool Edit(Form form, Host host, IWin32Window owner)
    {
        form.Text = "Control Properties";
        form.ShowInTaskbar = owner == null;
        form.StartPosition = owner == null ? FormStartPosition.CenterScreen : FormStartPosition.CenterParent;
        form.MinimizeBox = false;
        var size = host.Size;
        var grid = new PropertyGrid { Dock = DockStyle.Right, Width = 320, SelectedObject = host.Instance };
        var buttons = new FlowLayoutPanel
        {
            Dock = DockStyle.Bottom, AutoSize = true, FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(6)
        };
        var cancel = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel };
        var ok = new Button { Text = "OK", DialogResult = DialogResult.OK };
        buttons.Controls.AddRange(new Control[] { cancel, ok });
        // the control keeps its size on the page, whatever the window's
        var view = new Panel { Dock = DockStyle.Fill, AutoScroll = true, Padding = new Padding(8) };
        host.Dock = DockStyle.None;
        host.Location = new Point(8, 8);
        view.Controls.Add(host);
        host.Size = size;
        form.Controls.Add(view);
        form.Controls.Add(grid);
        form.Controls.Add(buttons);
        form.ClientSize = new Size(
            Math.Min(Math.Max(size.Width + 16, 320) + grid.Width, 1400),
            Math.Min(Math.Max(size.Height + 16, 360) + 48, 900));
        form.AcceptButton = ok;
        form.CancelButton = cancel;
        return form.ShowDialog(owner) == DialogResult.OK;
    }

    [DllImport("user32.dll")]
    private static extern bool PrintWindow(IntPtr hwnd, IntPtr dc, int flags);

    private static bool Plain(Bitmap image)
    {
        Color first = image.GetPixel(0, 0);
        for (int y = 0; y < image.Height; y += 3)
            for (int x = 0; x < image.Width; x += 3)
                if (image.GetPixel(x, y) != first)
                    return false;
        return true;
    }

    // The window a picture is taken in: on the screen (a WPF view such as the Scheduler's
    // paints only there) but fully transparent, never activated, clicked or in the taskbar.
    private sealed class Stage : Form
    {
        protected override bool ShowWithoutActivation { get { return true; } }

        protected override CreateParams CreateParams
        {
            get
            {
                var p = base.CreateParams;
                p.ExStyle |= 0x80 | 0x20 | 0x08000000;  // TOOLWINDOW, TRANSPARENT, NOACTIVATE
                return p;
            }
        }
    }

    // The control shown in the stage and given time to settle: active as on a page, so it
    // paints, and saves (a VB6 control that is not active fails to save: error 387 'Width').
    private static void Present(Form stage, Host host, int width, int height)
    {
        stage.FormBorderStyle = FormBorderStyle.None;
        stage.StartPosition = FormStartPosition.Manual;
        stage.Location = new Point(0, 0);
        stage.Opacity = 0.0;
        stage.ClientSize = new Size(width, height);
        host.Dock = DockStyle.Fill;
        stage.Show();
        var clock = System.Diagnostics.Stopwatch.StartNew();
        while (clock.ElapsedMilliseconds < 1500)
        {
            Application.DoEvents();
            System.Threading.Thread.Sleep(20);
        }
    }

    // The control's state saved into a new compound file at ``path``: its bytes.
    private static byte[] SaveState(IPersistStorage persist, Guid id, string path)
    {
        IntPtr saved;
        StgCreateDocfile(path, StgmCreate | StgmReadWrite | StgmShareExclusive, 0, out saved);
        try
        {
            WriteClassStg(saved, id);
            persist.Save(saved, false);
            persist.SaveCompleted(IntPtr.Zero);
        }
        finally { Marshal.Release(saved); }
        return File.ReadAllBytes(path);
    }

    // The sectors of the compound file's directory (its header's first sector, then the FAT
    // chain; the FAT sectors are the header's first 109).
    private static System.Collections.Generic.HashSet<int> DirectorySectors(byte[] f)
    {
        var dir = new System.Collections.Generic.HashSet<int>();
        int size = 1 << BitConverter.ToUInt16(f, 30);
        int fatSectors = BitConverter.ToInt32(f, 44);
        var fat = new System.Collections.Generic.List<int>();
        for (int k = 0; k < Math.Min(fatSectors, 109); k++)
        {
            int at = (BitConverter.ToInt32(f, 76 + 4 * k) + 1) * size;
            for (int j = 0; j < size && at + j + 4 <= f.Length; j += 4)
                fat.Add(BitConverter.ToInt32(f, at + j));
        }
        int s = BitConverter.ToInt32(f, 48);
        while (s >= 0 && s < fat.Count && dir.Add(s))
            s = fat[s];
        return dir;
    }

    // The same saved state: the bytes are the same but for the directory entries' created and
    // modified times (the control saves the same state twice with new times).
    private static bool SameState(byte[] a, byte[] b)
    {
        if (a.Length != b.Length)
            return false;
        int size = 1 << BitConverter.ToUInt16(a, 30);
        System.Collections.Generic.HashSet<int> dir = null;
        for (int i = 0; i < a.Length; i++)
        {
            if (a[i] == b[i])
                continue;
            if (dir == null)
                dir = DirectorySectors(a);
            int sector = i / size - 1, inEntry = i % size % 128;
            if (i < size || !dir.Contains(sector) || inEntry < 100 || inEntry >= 116)
                return false;
        }
        return true;
    }

    // A capture of one colour means the control paints nothing outside its own editor: no
    // picture then (false).
    private static bool Draw(Host host, int width, int height, string file)
    {
        using (var image = new Bitmap(width, height, PixelFormat.Format24bppRgb))
        {
            using (var g = Graphics.FromImage(image))
            {
                g.Clear(SystemColors.Window);
                IntPtr dc = g.GetHdc();
                try { PrintWindow(host.Handle, dc, 2); }  // PW_RENDERFULLCONTENT: WPF too
                finally { g.ReleaseHdc(dc); }
            }
            if (Plain(image))
                return false;
            image.Save(file, ImageFormat.Png);
            return true;
        }
    }

    private static void UseBin()
    {
        string bin = Environment.GetEnvironmentVariable("CTG_CONTROL_BIN");
        if (String.IsNullOrEmpty(bin) || !Directory.Exists(bin))
            return;
        Environment.SetEnvironmentVariable("PATH", bin + ";" + Environment.GetEnvironmentVariable("PATH"));
        AppDomain.CurrentDomain.AssemblyResolve += delegate(object sender, ResolveEventArgs e)
        {
            string name = new AssemblyName(e.Name).Name;
            if (Path.GetFileName(name) != name)
                return null;
            string dll = Path.Combine(bin, name + ".dll");
            return File.Exists(dll) ? Assembly.LoadFrom(dll) : null;
        };
    }

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            if (args.Length < 6 || args.Length > 7)
                throw new ArgumentException("Usage: ctgctl draw|edit <clsid> <state> <output> <width> <height> [owner]");
            string mode = args[0], state = args[2], output = args[3];
            bool edit = mode == "edit";
            if (!edit && mode != "draw")
                throw new ArgumentException("Unknown request: " + mode);
            Guid expected = new Guid(args[1]);
            int width = Int32.Parse(args[4]), height = Int32.Parse(args[5]);
            if (width < 1 || height < 1 || width > 4096 || height > 4096)
                throw new ArgumentException("The control's size is out of range.");
            UseBin();
            Application.EnableVisualStyles();
            Application.OleRequired();
            IntPtr storage;
            StgOpenStorage(state, IntPtr.Zero, StgmShareExclusive, IntPtr.Zero, 0, out storage);
            Guid id;
            try { ReadClassStg(storage, out id); }
            finally { Marshal.Release(storage); }
            if (id != expected)
                throw new InvalidDataException("The saved state belongs to another control.");
            using (var stage = new Stage())
            using (var site = new DesignContainer())
            using (var host = new Host(id))
            {
                stage.ShowInTaskbar = false;
                stage.ClientSize = new Size(width, height);
                site.Add(host);
                stage.Controls.Add(host);
                host.Size = new Size(width, height);
                // the saved state goes in before the control is made, so it is loaded
                // (IPersistStorage.Load), never first set up as a new control: a VB6 control
                // (Database Exchange) fails its new-control set-up in a designer (error 387)
                host.OcxState = new AxHost.State(new MemoryStream(File.ReadAllBytes(state)), 2, false, null);
                IntPtr made = stage.Handle;
                host.CreateControl();
                Present(stage, host, width, height);
                if (!edit)
                    return Draw(host, width, height, output) ? 0 : 3;
                var persist = (IPersistStorage)host.Instance;
                // what the control saves before any change: the same bytes after the pages
                // mean nothing changed (or Cancel; a VB6 control reports itself changed always)
                byte[] before = SaveState(persist, id, output + ".before");
                File.Delete(output + ".before");
                var pages = new Pages();
                var provider = host.Instance as ISpecifyPropertyPages;
                if (provider != null)
                    provider.GetPages(out pages);
                IntPtr ownerHandle = args.Length == 7 ? new IntPtr(Int64.Parse(args[6])) : IntPtr.Zero;
                byte[] after;
                try
                {
                    if (pages.Count > 0)
                    {
                        // the control's own property pages, as the Graphics Builder shows them
                        // (a property list over a control's COM properties corrupts some, the
                        // Process Analyst: its save then crashes)
                        IntPtr unk = Marshal.GetIUnknownForObject(host.Instance);
                        try
                        {
                            OleCreatePropertyFrame(ownerHandle, 0, 0, "Control", 1, ref unk, pages.Count, pages.Ids, 0, 0, IntPtr.Zero);
                        }
                        finally { Marshal.Release(unk); }
                        after = SaveState(persist, id, output);
                    }
                    else
                    {
                        using (var dialog = new Form())
                        {
                            stage.Controls.Remove(host);
                            if (!Edit(dialog, host, ownerHandle == IntPtr.Zero ? null : new Owner(ownerHandle.ToInt64())))
                                return 2;
                            after = SaveState(persist, id, output);  // (before the window takes the control with it)
                        }
                    }
                }
                finally
                {
                    if (pages.Ids != IntPtr.Zero)
                        Marshal.FreeCoTaskMem(pages.Ids);
                }
                if (SameState(before, after))
                {
                    File.Delete(output);
                    return 2;
                }
            }
            return 0;
        }
        catch (Exception e)
        {
            Console.Error.WriteLine(e.GetBaseException().Message);
            return 1;
        }
    }
}
