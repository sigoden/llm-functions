import os
import platform

def get_sysinfo():
  """
  Get the system info
  """
  return "\n".join([
    f"OS: {platform.system()}",
    f"Arch: {platform.machine()}",
  ])