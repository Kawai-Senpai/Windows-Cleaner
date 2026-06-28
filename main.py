# main.py
# Entry point. Launches the GUI. The GUI itself offers a "Restart as Admin"
# button rather than force-elevating, so you can review non-admin tasks first.
#
#   python main.py            -> launch GUI
#   python main.py --admin    -> relaunch elevated immediately

import sys
import os

# allow running from anywhere
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import engine


def main():
    if os.name != "nt":
        print("This tool is for Windows only.")
        sys.exit(1)

    if "--admin" in sys.argv and not engine.is_admin():
        engine.relaunch_as_admin()
        sys.exit(0)

    from gui.app import run
    run()


if __name__ == "__main__":
    main()
