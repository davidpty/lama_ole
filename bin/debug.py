#!/usr/bin/env python3

import sys
import os

def main():
    # Get the directory of this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # The parent directory is lama_ole/
    parent_dir = os.path.dirname(script_dir)
    # Path to lama_ole.py
    lama_ole_py = os.path.join(parent_dir, "lama_ole.py")
    
    # Prepare arguments for the subprocess call
    # We want to run: python3 -i lama_ole/lama_ole.py --debug [other args]
    args = [sys.executable, "-i", lama_ole_py, "--debug"] + sys.argv[1:]
    
    # Use os.execv to replace the current process with the new one
    os.execv(sys.executable, args)

if __name__ == "__main__":
    main()
