"""Native AppKit frontend loaded by the precompiled py2app launcher."""
from __future__ import annotations

import os
import shutil
import sys
import traceback
from pathlib import Path

import objc
from Foundation import NSObject, NSURL, NSFileManager, NSDownloadsDirectory, NSUserDomainMask
from AppKit import (
    NSApplication, NSApplicationActivationPolicyRegular, NSApplicationDelegateReplySuccess,
    NSTerminateNow, NSTerminateLater, NSWindow, NSView, NSWindowStyleMaskTitled,
    NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable, NSBackingStoreBuffered,
    NSTextField, NSFont, NSColor, NSButton, NSProgressIndicator, NSProgressIndicatorBarStyle,
    NSBezelStyleRounded, NSOpenPanel, NSModalResponseOK, NSWorkspace, NSSound, NSMenu,
    NSMenuItem, NSPasteboardTypeFileURL, NSPasteboardURLReadingFileURLsOnlyKey,
    NSDragOperationCopy, NSDragOperationNone, NSBezierPath,
)
from PyObjCTools import AppHelper
from mimetic_macos.cocoa_protocols import selector_for_protocol
from mimetic_portable.desktop_runner import Runner
from mimetic_portable.runtime import command, logs_dir


def protocol_method(protocol_name, selector_name):
    return selector_for_protocol(objc, protocol_name, selector_name)


class MimeticDropView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(MimeticDropView, self).initWithFrame_(frame)
        if self is not None:
            self.registerForDraggedTypes_([NSPasteboardTypeFileURL])
            self.handler = None
        return self

    def drawRect_(self, rect):
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 14.0, 14.0)
        NSColor.controlBackgroundColor().setFill()
        path.fill()
        NSColor.separatorColor().setStroke()
        path.setLineWidth_(1.0)
        path.stroke()

    @objc.python_method
    def files(self, sender):
        urls = sender.draggingPasteboard().readObjectsForClasses_options_(
            [NSURL], {NSPasteboardURLReadingFileURLsOnlyKey: True}) or []
        return [Path(str(url.path())) for url in urls
                if url.isFileURL() and str(url.pathExtension()).lower() == "zip"]

    @protocol_method("NSDraggingDestination", b"draggingEntered:")
    def draggingEntered_(self, sender):
        return NSDragOperationCopy if self.files(sender) else NSDragOperationNone

    @protocol_method("NSDraggingDestination", b"performDragOperation:")
    def performDragOperation_(self, sender):
        files = self.files(sender)
        if files and self.handler:
            self.handler(files)
            return True
        return False


class MimeticDelegate(NSObject, protocols=[objc.protocolNamed("NSApplicationDelegate"), objc.protocolNamed("NSWindowDelegate")]):
    def init(self):
        self = objc.super(MimeticDelegate, self).init()
        if self is not None:
            self.pending = []
            self.runner = None
            self.window = None
            self.current_input = self.output = self.destination = self.workspace = None
            self.ready = self.error = None
            self.last_log = None
            self.launched = self.quitting = False
        return self

    @protocol_method("NSApplicationDelegate", b"applicationDidFinishLaunching:")
    def applicationDidFinishLaunching_(self, notification):
        self.ensure_window()
        self.launched = True
        self.start_next()

    @protocol_method("NSApplicationDelegate", b"application:openFiles:")
    def application_openFiles_(self, application, names):
        self.enqueue([Path(str(name)) for name in names])
        application.replyToOpenOrPrint_(NSApplicationDelegateReplySuccess)

    @protocol_method("NSApplicationDelegate", b"applicationShouldHandleReopen:hasVisibleWindows:")
    def applicationShouldHandleReopen_hasVisibleWindows_(self, application, visible):
        self.ensure_window()
        self.window.makeKeyAndOrderFront_(None)
        return True

    @protocol_method("NSWindowDelegate", b"windowShouldClose:")
    def windowShouldClose_(self, window):
        NSApplication.sharedApplication().terminate_(None)
        return False

    @protocol_method("NSApplicationDelegate", b"applicationShouldTerminate:")
    def applicationShouldTerminate_(self, application):
        if self.runner is None:
            return NSTerminateNow
        self.quitting = True
        self.pending.clear()
        self.cancelCurrent_(None)
        return NSTerminateLater

    @objc.python_method
    def label(self, text, frame, size=13, secondary=False):
        view = NSTextField.labelWithString_(text)
        view.setFrame_(frame)
        view.setFont_(NSFont.systemFontOfSize_(size))
        if secondary:
            view.setTextColor_(NSColor.secondaryLabelColor())
        self.window.contentView().addSubview_(view)
        return view

    @objc.python_method
    def button(self, title, action, frame):
        button = NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setBezelStyle_(NSBezelStyleRounded)
        button.setTarget_(self)
        button.setAction_(action)
        self.window.contentView().addSubview_(button)
        return button

    @objc.python_method
    def ensure_window(self):
        if self.window is not None:
            return
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (660, 405)), NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable,
            NSBackingStoreBuffered, False)
        self.window.setTitle_("Mimetic · Pro Tools")
        self.window.setReleasedWhenClosed_(False)
        self.window.setDelegate_(self)
        self.window.center()
        self.label("Mimetic", ((26, 344), (250, 40)), 28)
        self.label("1.5 · Mac Intel", ((468, 353), (165, 20)), 12, True)
        drop = MimeticDropView.alloc().initWithFrame_(((26, 207), (608, 119)))
        drop.handler = self.enqueue
        self.window.contentView().addSubview_(drop)
        title = NSTextField.labelWithString_("Dépose ta séquence ZIP ici")
        title.setFrame_(((30, 62), (550, 29)))
        title.setFont_(NSFont.systemFontOfSize_(20))
        title.setAlignment_(1)
        drop.addSubview_(title)
        hint = NSTextField.labelWithString_("Un fichier .ptx et son dossier Audio Files")
        hint.setFrame_(((30, 33), (550, 20)))
        hint.setAlignment_(1)
        hint.setTextColor_(NSColor.secondaryLabelColor())
        drop.addSubview_(hint)
        self.status = self.label("Prêt à traiter une séquence Pro Tools.", ((26, 170), (608, 25)), 14)
        self.progress = NSProgressIndicator.alloc().initWithFrame_(((26, 148), (608, 13)))
        self.progress.setStyle_(NSProgressIndicatorBarStyle)
        self.progress.setMinValue_(0)
        self.progress.setMaxValue_(1)
        self.progress.setIndeterminate_(False)
        self.window.contentView().addSubview_(self.progress)
        self.detail = NSTextField.wrappingLabelWithString_("Le ZIP de sortie sera créé dans Téléchargements.")
        self.detail.setFrame_(((26, 91), (608, 45)))
        self.detail.setFont_(NSFont.systemFontOfSize_(12))
        self.detail.setTextColor_(NSColor.secondaryLabelColor())
        self.window.contentView().addSubview_(self.detail)
        self.choose = self.button("Choisir un ZIP…", "chooseZIP:", ((23, 47), (154, 32)))
        self.choose.setKeyEquivalent_("\r")
        self.cancel = self.button("Annuler", "cancelCurrent:", ((179, 47), (88, 32)))
        self.cancel.setEnabled_(False)
        self.reveal = self.button("Afficher le résultat", "revealResult:", ((269, 47), (174, 32)))
        self.reveal.setEnabled_(False)
        self.button("Voir le journal", "showLog:", ((445, 47), (143, 32)))
        self.label("IR : Clip Gain +3,5 dB  ·  MIX mutés  ·  Traitement local", ((26, 16), (608, 20)), 11, True)
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.python_method
    def enqueue(self, paths):
        self.ensure_window()
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        candidates = [path.absolute() for path in paths if path.suffix.lower() == ".zip"]
        if not candidates:
            self.status.setStringValue_("Choisis un ZIP contenant une session Pro Tools.")
            return
        for path in candidates:
            if path != self.current_input and path not in self.pending:
                self.pending.append(path)
        if self.runner:
            self.detail.setStringValue_(f"{self.current_input.name} · {len(self.pending)} ZIP en attente")
        elif self.launched:
            self.start_next()

    def chooseZIP_(self, sender):
        panel = NSOpenPanel.openPanel()
        panel.setAllowedFileTypes_(["zip"])
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(True)
        if panel.runModal() == NSModalResponseOK:
            self.enqueue([Path(str(url.path())) for url in panel.URLs()])

    @objc.python_method
    def start_next(self):
        if self.runner or not self.pending or self.quitting:
            return
        archive = self.pending.pop(0)
        directories = NSFileManager.defaultManager().URLsForDirectory_inDomains_(
            NSDownloadsDirectory, NSUserDomainMask)
        destination = Path(str(directories[0].path())) if directories else Path.home() / "Downloads"
        try:
            destination.mkdir(parents=True, exist_ok=True)
            destination = destination.resolve(strict=True)
            available = destination.is_dir() and os.access(destination, os.W_OK)
        except OSError:
            available = False
        if not available:
            panel = NSOpenPanel.openPanel()
            panel.setTitle_("Choisir le dossier du ZIP de sortie")
            panel.setMessage_("Le dossier Téléchargements n’est pas accessible en écriture.")
            panel.setCanChooseFiles_(False)
            panel.setCanChooseDirectories_(True)
            panel.setCanCreateDirectories_(True)
            if panel.runModal() != NSModalResponseOK:
                self.start_next()
                return
            destination = Path(str(panel.URL().path())).resolve()
        self.current_input, self.destination = archive, destination
        self.output = self.workspace = self.ready = self.error = None
        self.reveal.setEnabled_(False)
        self.cancel.setEnabled_(True)
        self.status.setStringValue_("Préparation du traitement…")
        self.detail.setStringValue_(archive.name)
        self.progress.setIndeterminate_(True)
        self.progress.startAnimation_(None)
        def deliver(token, message):
            AppHelper.callAfter(self.receive, token, message)
        self.runner = Runner(command(), deliver, logs_dir())
        self.last_log = self.runner.log_path
        try:
            self.runner.start(archive, destination)
        except Exception as exc:
            traceback.print_exc()
            self.error = str(exc)
            self.finish(1, False)

    @objc.python_method
    def receive(self, token, message):
        if self.runner is None or self.runner.token != token:
            return
        kind = message.get("event")
        if kind == "workspace":
            path = Path(message["path"])
            if path.name.startswith(".mimetic-") and path.parent == self.destination:
                self.workspace = path
            return
        if kind == "exit":
            self.finish(message["return_code"], message["cancelled"])
            return
        if self.runner.cancelled:
            return
        if kind == "progress":
            self.status.setStringValue_(message.get("step", "Traitement…"))
            total = message.get("total", 0)
            if total:
                self.progress.stopAnimation_(None)
                self.progress.setIndeterminate_(False)
                self.progress.setDoubleValue_(message.get("completed", 0) / total)
        elif kind == "plan":
            self.detail.setStringValue_(f"{self.current_input.name} · {message['total']} ADR · {len(self.pending)} ZIP en attente")
        elif kind == "ready":
            self.ready = message
            self.cancel.setEnabled_(False)
        elif kind == "error":
            error = message.get("error", {})
            self.error = error.get("message", "Le traitement a échoué.")
            if error.get("detail"):
                self.error += "\n" + error["detail"]

    @objc.python_method
    def finish(self, code, cancelled):
        self.runner = None
        if self.workspace and self.workspace.name.startswith(".mimetic-") and self.workspace.parent == self.destination:
            shutil.rmtree(self.workspace, ignore_errors=True)
        self.workspace = None
        self.progress.stopAnimation_(None)
        self.progress.setIndeterminate_(False)
        self.cancel.setEnabled_(False)
        if cancelled or code == 130:
            self.progress.setDoubleValue_(0)
            self.status.setStringValue_("Traitement annulé.")
            self.detail.setStringValue_("L’archive originale est conservée.")
        elif code == 0 and self.ready and Path(self.ready["path"]).is_file():
            self.output = Path(self.ready["path"])
            self.progress.setDoubleValue_(1)
            self.reveal.setEnabled_(True)
            self.status.setStringValue_(f"Terminé · {self.ready['total']} ADR traités")
            warnings = len(self.ready.get("warnings", []))
            text = self.output.name
            if warnings:
                text += f" · {warnings} ADR avec avertissements dans Mimetic_report.json"
            self.detail.setStringValue_(text)
            self.revealResult_(None)
        else:
            self.progress.setDoubleValue_(0)
            self.status.setStringValue_("Le traitement a échoué.")
            self.detail.setStringValue_(self.error or f"Consulte le journal (code {code}).")
            NSSound.beep()
        self.current_input = None
        if self.quitting:
            NSApplication.sharedApplication().replyToApplicationShouldTerminate_(True)
        else:
            self.start_next()

    def cancelCurrent_(self, sender):
        if self.runner:
            self.cancel.setEnabled_(False)
            self.status.setStringValue_("Annulation…")
            self.runner.cancel()

    def revealResult_(self, sender):
        if self.output:
            NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([NSURL.fileURLWithPath_(str(self.output))])

    def showLog_(self, sender):
        path = self.last_log or logs_dir()
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(path)))


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = MimeticDelegate.alloc().init()
    app.setDelegate_(delegate)
    menu = NSMenu.alloc().init()
    item = NSMenuItem.alloc().init()
    menu.addItem_(item)
    submenu = NSMenu.alloc().init()
    submenu.addItemWithTitle_action_keyEquivalent_("À propos de Mimetic", "orderFrontStandardAboutPanel:", "")
    submenu.addItem_(NSMenuItem.separatorItem())
    submenu.addItemWithTitle_action_keyEquivalent_("Quitter Mimetic", "terminate:", "q")
    item.setSubmenu_(submenu)
    app.setMainMenu_(menu)
    AppHelper.runEventLoop(pdb=False)
