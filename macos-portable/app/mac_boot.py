"""Boot script run by the unmodified, precompiled py2app app launcher."""
import os
import plistlib
import sys
from datetime import datetime
from pathlib import Path

resources = Path(os.environ["RESOURCEPATH"])
sys.path[:] = [str(resources / "app"), str(resources / "payload"),
               str(resources / "lib" / "python3.11" / "site-packages"),
               str(resources / "lib" / "python3.11"),
               str(resources / "lib" / "python3.11" / "lib-dynload"), str(resources)]
sys.executable = str(resources / "bin" / "python3.11")
sys.dont_write_bytecode = True
os.environ["PYTHONHOME"] = str(resources)
os.environ["PYTHONNOUSERSITE"] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["PYTHONUTF8"] = "1"
log_dir = Path.home() / "Library" / "Logs" / "Mimetic"
log_dir.mkdir(parents=True, exist_ok=True)
sys.stdout = sys.stderr = (log_dir / "app.log").open("a", encoding="utf-8", buffering=1)

try:
    bundle_info = plistlib.loads((resources.parent / "Info.plist").read_bytes())
    print(f"\n--- Mimetic build {bundle_info.get('CFBundleVersion', '?')} "
          f"| {datetime.now().isoformat(timespec='seconds')} | {resources.parent.parent} ---", flush=True)
    from mimetic_macos.cocoa_app import main
    main()
except BaseException:
    import traceback
    traceback.print_exc()
    # Visible startup diagnostics even if the Cocoa bridge could not load.
    import subprocess
    subprocess.run(["/usr/bin/osascript", "-e",
                    'display alert "Mimetic ne peut pas démarrer" message "Consulte ~/Library/Logs/Mimetic/app.log."'], check=False)
