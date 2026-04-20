from utils import *
# This runs every time a user attaches or re-attaches to the environment

# Refresh blog and video indexes in the background so the navigator stays current
for subcmd in ("blog", "video"):
    subprocess.Popen(
        [sys.executable, f"{BASE_DIR}/index_updater.py", subcmd],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )