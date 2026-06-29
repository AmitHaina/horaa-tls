import os
import sys
import platform
import ctypes
import json
import urllib.request
from typing import Tuple, Optional

from horaa_tls.exceptions import BackendError

OWNER = "bogdanfinn"
REPO = "tls-client"
RELEASES_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"


def get_root_dir() -> str:
    """Returns the absolute root directory of the package."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_dependencies_dir() -> str:
    """Returns the path to the dependencies folder where shared libs are stored."""
    deps_dir = os.path.join(get_root_dir(), "dependencies")
    os.makedirs(deps_dir, exist_ok=True)
    return deps_dir


def get_system_platform() -> Tuple[str, str]:
    """
    Returns (system_os, architecture).
    system_os: 'windows', 'darwin', 'linux'
    architecture: 'amd64', 'arm64', '386', '32', '64'
    """
    system_os = platform.system().lower()
    machine = platform.machine().lower()

    # Normalize system os
    if system_os == "darwin":
        sys_os = "darwin"
        arch = "arm64" if machine == "arm64" else "amd64"
    elif system_os in ("windows", "win32", "cygwin"):
        sys_os = "windows"
        arch = "64" if ctypes.sizeof(ctypes.c_voidp) == 8 else "32"
    else:
        sys_os = "linux"
        if machine == "aarch64":
            arch = "arm64"
        elif "x86" in machine or machine == "amd64":
            arch = "amd64"
        else:
            arch = "amd64"  # Default fallback

    return sys_os, arch


def generate_asset_name(version: str) -> str:
    """
    Generates the exact asset filename expected on GitHub releases.
    Example: tls-client-windows-64-1.7.8.dll or tls-client-darwin-arm64-1.7.8.dylib
    """
    sys_os, arch = get_system_platform()
    
    if sys_os == "windows":
        ext = ".dll"
    elif sys_os == "darwin":
        ext = ".dylib"
    else:
        ext = ".so"
        # Check for Alpine vs Ubuntu/generic glibc distributions
        if os.path.exists("/etc/alpine-release"):
            sys_os = "linux-alpine"
        else:
            sys_os = "linux-ubuntu"

    return f"tls-client-{sys_os}-{arch}-{version}{ext}"


def fetch_latest_release_info() -> Tuple[str, list]:
    """
    Queries GitHub API to fetch latest version tag and asset list.
    Returns (version_str, asset_list).
    """
    req = urllib.request.Request(
        RELEASES_URL,
        headers={"User-Agent": "horaa-tls-updater", "Accept": "application/vnd.github.v3+json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            version = data["tag_name"].replace("v", "")
            return version, data.get("assets", [])
    except Exception as e:
        raise BackendError(f"Failed to fetch latest release info from GitHub: {e}")


def read_local_version() -> Tuple[Optional[str], Optional[str]]:
    """Reads current local shared lib asset name and version from .version file."""
    deps_dir = get_dependencies_dir()
    version_file = os.path.join(deps_dir, ".version")
    if not os.path.exists(version_file):
        return None, None
    try:
        with open(version_file, "r") as f:
            content = f.read().strip().split(" ")
            if len(content) == 2:
                return content[0], content[1]
    except Exception:
        pass
    return None, None


def save_local_version(asset_name: str, version: str):
    """Saves the local asset name and version to the .version file."""
    deps_dir = get_dependencies_dir()
    version_file = os.path.join(deps_dir, ".version")
    with open(version_file, "w") as f:
        f.write(f"{asset_name} {version}")


def download_asset(url: str, dest_path: str):
    """Downloads the file from the given url to dest_path."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "horaa-tls-updater"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response, open(dest_path, "wb") as out_file:
            out_file.write(response.read())
    except Exception as e:
        if os.path.exists(dest_path):
            os.remove(dest_path)
        raise BackendError(f"Failed to download asset: {e}")


def update_if_necessary() -> str:
    """
    Checks if shared library needs downloading or updating.
    Downloads if necessary, and returns the path to the loaded library file.
    """
    deps_dir = get_dependencies_dir()
    local_asset, local_version = read_local_version()

    # Fetch latest version info
    latest_version, assets = fetch_latest_release_info()
    expected_asset_name = generate_asset_name(latest_version)
    target_path = os.path.join(deps_dir, expected_asset_name)

    # Check if local matches latest
    if local_version == latest_version and local_asset == expected_asset_name and os.path.exists(target_path):
        return target_path

    # Find the download URL for the expected asset
    download_url = None
    for asset in assets:
        if asset["name"] == expected_asset_name:
            download_url = asset["browser_download_url"]
            break

    if not download_url:
        # Check if there is an existing library and we failed to fetch a match
        if local_asset and os.path.exists(os.path.join(deps_dir, local_asset)):
            print(f"[horaa-tls] Warning: Could not find latest asset {expected_asset_name} on GitHub. Using cached version {local_asset}.")
            return os.path.join(deps_dir, local_asset)
        raise BackendError(f"Target asset '{expected_asset_name}' was not found in the latest GitHub release.")

    print(f"[horaa-tls] Downloading precompiled Go tls-client dynamic library: {expected_asset_name}...")
    download_asset(download_url, target_path)
    save_local_version(expected_asset_name, latest_version)
    
    # Remove older files in dependencies folder to keep it clean
    for file in os.listdir(deps_dir):
        if file not in (expected_asset_name, ".version") and not file.startswith("."):
            try:
                os.remove(os.path.join(deps_dir, file))
            except Exception:
                pass

    return target_path
