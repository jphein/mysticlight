import os

# Pin device identity before mysticlight.bridge is imported so test
# expectations don't depend on the machine's hostname.
os.environ.setdefault("DEVICE_SLUG", "katana")
os.environ.setdefault("DEVICE_NAME", "Mystic Light — katana")
