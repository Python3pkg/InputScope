# -*- coding: utf-8 -*-
"""
InputScope main entrance, runs a tray application if wx available, a simple
command-line echoer otherwise. Launches the event listener and web UI server.

@author      Erki Suurjaak
@created     05.05.2015
@modified    24.04.2015
"""
import multiprocessing
import multiprocessing.forking
import os
import signal
import sys
import threading
import webbrowser
try: import Tkinter as tk   # For getting screen size if wx unavailable
except ImportError: tk = None
try: import win32com.client # For creating startup shortcut
except ImportError: pass
try: import wx, wx.lib.sized_controls, wx.py.shell
except ImportError: wx = None

import conf
import db
import listener
import webui

class Popen(multiprocessing.forking.Popen):
    """Support for PyInstaller-frozen Windows executables."""
    def __init__(self, *args, **kwargs):
        hasattr(sys, "frozen") and os.putenv("_MEIPASS2", sys._MEIPASS + os.sep)
        try: super(Popen, self).__init__(*args, **kwargs)
        finally: hasattr(sys, "frozen") and os.unsetenv("_MEIPASS2")

class Process(multiprocessing.Process): _Popen = Popen


class Model(threading.Thread):
    """Input monitor main runner model."""

    def __init__(self, messagehandler=None):
        """
        @param   messagehandler  function to invoke with incoming messages
        """
        threading.Thread.__init__(self)
        self.messagehandler = messagehandler
        self.running = False
        self.listenerqueue = multiprocessing.Queue() # Out-queue to listener
        self.webqueue      = multiprocessing.Queue() # Out-queue to webui
        self.inqueue       = multiprocessing.Queue() # In-queue from listener


    def toggle(self, input):
        if "mouse" == input:
            enabled = conf.MouseEnabled = not conf.MouseEnabled
        elif "keyboard" == input:
            enabled = conf.KeyboardEnabled = not conf.KeyboardEnabled
        self.listenerqueue.put("%s_%s" % (input, "start" if enabled else "stop"))
        conf.save()

    def log_resolution(self, size):
        if size: db.insert("screen_sizes", x=size[0], y=size[1])

    def run(self):
        try: signal.signal(signal.SIGINT, lambda x: None) # Do not propagate Ctrl-C
        except ValueError: pass
        listenerargs = self.listenerqueue, self.inqueue
        Process(target=start_listener, args=listenerargs).start()
        Process(target=start_webui, args=(self.webqueue,)).start()
        try: signal.signal(signal.SIGINT, signal.default_int_handler)
        except ValueError: pass

        if conf.MouseEnabled:    self.listenerqueue.put("mouse_start")
        if conf.KeyboardEnabled: self.listenerqueue.put("keyboard_start")

        self.running = True
        while self.running:
            data = self.inqueue.get()
            if not data: continue
            self.messagehandler and self.messagehandler(data)

    def stop(self):
        self.running = False
        self.listenerqueue.put("exit"), self.webqueue.put("exit")
        self.inqueue.put(None) # Wake up thread waiting on queue



class MainWindow(getattr(wx, "Frame", object)):
    def __init__(self):
        wx.Frame.__init__(self, parent=None,
                          title="%s %s" % (conf.Title, conf.Version))

        handler = lambda x: wx.CallAfter(lambda: log and log.SetValue(str(x)))
        self.model = Model(handler)
        self.startupservice = StartupService()

        self.frame_console = wx.py.shell.ShellFrame(self)
        self.trayicon = wx.TaskBarIcon()

        if os.path.exists(conf.IconPath):
            icons = wx.IconBundle()
            icons.AddIconFromFile(conf.IconPath, wx.BITMAP_TYPE_ICO)
            self.SetIcons(icons)
            self.frame_console.SetIcons(icons)
            self.trayicon.SetIcon(icons.GetIconOfExactSize((16, 16)), conf.Title)

        panel = wx.lib.sized_controls.SizedPanel(self)
        log = self.log = wx.TextCtrl(panel, style=wx.TE_MULTILINE)

        log.SetEditable(False)
        log.BackgroundColour = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
        log.ForegroundColour = wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)
        self.frame_console.Title = "%s Console" % conf.Title

        panel.SetSizerType("vertical")
        log.SetSizerProps(expand=True, proportion=1)

        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self.Bind(wx.EVT_DISPLAY_CHANGED, self.OnDisplayChanged)
        self.trayicon.Bind(wx.EVT_TASKBAR_LEFT_DCLICK, self.OnOpenUI)
        self.trayicon.Bind(wx.EVT_TASKBAR_RIGHT_DOWN, self.OnOpenMenu)
        self.frame_console.Bind(wx.EVT_CLOSE, self.OnToggleConsole)

        self.Show()
        self.model.start()


    def OnOpenMenu(self, event):
        """Creates and opens a popup menu for the tray icon."""
        menu = wx.Menu()
        item_ui = wx.MenuItem(menu, -1, "&Open user interface")
        item_startup = wx.MenuItem(menu, -1, "&Start with Windows", 
            kind=wx.ITEM_CHECK) if self.startupservice.can_start() else None
        item_mouse = wx.MenuItem(menu, -1, "Stop &mouse logging",
                                 kind=wx.ITEM_CHECK)
        item_keyboard = wx.MenuItem(menu, -1, "Stop &keyboard logging",
                                    kind=wx.ITEM_CHECK)
        item_console = wx.MenuItem(menu, -1, "Show Python &console",
                                   kind=wx.ITEM_CHECK)
        item_exit = wx.MenuItem(menu, -1, "E&xit %s" % conf.Title)

        font = item_ui.Font
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        font.SetFaceName(self.Font.FaceName)
        font.SetPointSize(self.Font.PointSize)
        item_ui.Font = font

        menu.AppendItem(item_ui)
        menu.AppendItem(item_startup) if item_startup else None
        menu.AppendSeparator()
        menu.AppendItem(item_mouse)
        menu.AppendItem(item_keyboard)
        menu.AppendSeparator()
        menu.AppendItem(item_console)
        menu.AppendItem(item_exit)

        if item_startup: item_startup.Check(self.startupservice.is_started())
        item_mouse.Check(not conf.MouseEnabled)
        item_keyboard.Check(not conf.KeyboardEnabled)
        item_console.Check(self.frame_console.Shown)

        menu.Bind(wx.EVT_MENU, self.OnOpenUI,         id=item_ui.GetId())
        menu.Bind(wx.EVT_MENU, self.OnToggleStartup,  id=item_startup.GetId()) \
        if item_startup else None
        menu.Bind(wx.EVT_MENU, self.OnToggleMouse,    id=item_mouse.GetId())
        menu.Bind(wx.EVT_MENU, self.OnToggleKeyboard, id=item_keyboard.GetId())
        menu.Bind(wx.EVT_MENU, self.OnToggleConsole,  id=item_console.GetId())
        menu.Bind(wx.EVT_MENU, self.OnClose,          id=item_exit.GetId())
        self.trayicon.PopupMenu(menu)


    def OnDisplayChanged(self, event=None):
        self.model.log_resolution(wx.GetDisplaySize())

    def OnOpenUI(self, event):
        webbrowser.open(conf.WebUrl)

    def OnToggleStartup(self, event):
        self.startupservice.stop() if self.startupservice.is_started() \
        else self.startupservice.start()

    def OnToggleMouse(self, event):
        self.model.toggle("mouse")

    def OnToggleKeyboard(self, event):
        self.model.toggle("keyboard")

    def OnToggleConsole(self, event):
        self.frame_console.Show(not self.frame_console.IsShown())

    def OnClose(self, event):
        self.model.stop(), self.trayicon.Destroy(), wx.Exit()


class StartupService(object):
    """
    Manages starting a program on system startup, if possible. Currently
    supports only Windows systems.
    """

    def can_start(self):
        """Whether startup can be set on this system at all."""
        return ("win32" == sys.platform)

    def is_started(self):
        """Whether the program has been added to startup."""
        return os.path.exists(self.get_shortcut_path())

    def start(self):
        """Sets the program to run at system startup."""
        shortcut_path = self.get_shortcut_path()
        target_path = conf.ExecutablePath
        workdir, icon = conf.ApplicationPath, conf.IconPath
        self.create_shortcut(shortcut_path, target_path, workdir, icon)

    def stop(self):
        """Stops the program from running at system startup."""
        try: os.unlink(self.get_shortcut_path())
        except Exception: pass

    def get_shortcut_path(self):
        path = "~\\Start Menu\\Programs\\Startup\\%s.lnk" % conf.Title
        return os.path.expanduser(path)

    def create_shortcut(self, path, target="", workdir="", icon=""):
        if "url" == path[-3:].lower():
            with open(path, "w") as shortcut:
                shortcut.write("[InternetShortcut]\nURL=%s" % target)
        else:
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(path)
            if target.lower().endswith(("py", "pyw")):
                # pythonw leaves no DOS window open
                python = sys.executable.replace("python.exe", "pythonw.exe")
                shortcut.Targetpath = '"%s"' % python
                shortcut.Arguments  = '"%s"' % target
            else:
                shortcut.Targetpath = target
            shortcut.WorkingDirectory = workdir
            if icon:
                shortcut.IconLocation = icon
            shortcut.save()


def start_webui(inqueue=None):
    """Starts the web server, with an optional incoming message queue."""
    def handle_commands(queue):
        while True:
            if queue.get() in ["exit", None]:
                os.kill(os.getpid(), signal.SIGINT) # Best way to shutdown bottle
    if inqueue: threading.Thread(target=handle_commands, args=(inqueue,)).start()
    webui.start()


def start_listener(inqueue, outqueue):
    """Starts the mouse and keyboard listener."""
    conf.init(), db.init(conf.DbPath)
    runner = listener.Listener(inqueue, outqueue)
    runner.run()



if "__main__" == __name__:
    multiprocessing.freeze_support()
    conf.init()
    db.init(conf.DbPath, conf.DbStatements)

    if wx:
        app = wx.App(redirect=True) # stdout and stderr redirected to wx popup
        app.SetTopWindow(MainWindow()) # stdout/stderr popup closes with window
        app.MainLoop()
    else:
        model = Model(lambda x: sys.stderr.write("\r%s" % x))
        if tk:
            widget = tk.Tk() # Use Tkinter instead to get screen size
            size = widget.winfo_screenwidth(), widget.winfo_screenheight()
            model.log_resolution(size)
        print("wxPython not available, using basic command line interface.")
        print("Web interface running at %s" % conf.WebUrl)
        try:
            model.run()
        except KeyboardInterrupt:
            model.stop()