import json
import os.path
import re
import subprocess
from urllib.request import urlopen, urlretrieve
from pathlib import Path
from git import Repo, GitCommandError

devices = [
    ("cupid", "Xiaomi HyperOS Global Stable"),
    ("unicorn", "Xiaomi HyperOS Stable"),
]

android_root = "/home/arian/android/lineage-23/"
vendor_root = "/home/arian/android/vendor/sm8450/"

review_url = "ssh://{}@review.lineageos.org:29418/LineageOS/{}"
review_user = "ArianK16a"
review_branch = "lineage-23.0"

hos_fans_url = "https://raw.githubusercontent.com/HegeKen/HyperData/refs/heads/main/devices/{}.json"
xiaomi_mirror_url = "https://bkt-sgp-miui-ota-update-alisgp.oss-ap-southeast-1.aliyuncs.com/{}/{}"


def version_key(version):
    # split by digit sequences
    return [int(i) for i in re.split(r"(\d+)", version) if i.isdigit()]


# fingerprint format: brand/name/device:release/build_id/incremental:build_type/tags
# build desc format: name-build_type version build_id incremental keys
def build_desc_from_fingerprint(fingerprint: str) -> str:
    device_info, version_info, build_info = fingerprint.split(":")

    brand, name, device = device_info.split("/")
    release, build_id, incremental = version_info.split("/")
    build_type, tags = build_info.split("/")

    return f"{name}-{build_type} {release} {build_id} {incremental} {tags}"


for codename, branch in devices:
    device_tree_path = os.path.join(android_root, "device", "xiaomi", codename)
    device_tree_repo = Repo(device_tree_path)
    if device_tree_repo.is_dirty():
        print(f"Skipping {codename} because the device_tree_repo is dirty!")
        continue

    vendor_tree_path = os.path.join(android_root, "vendor", "xiaomi", codename)
    vendor_tree_repo = Repo(vendor_tree_path)
    if vendor_tree_repo.is_dirty(untracked_files=True):
        print(f"Skipping {codename} because the vendor_tree_repo is dirty!")
        continue

    with urlopen(hos_fans_url.format(codename)) as url:
        data = json.loads(url.read().decode())

    branch = next(b for b in data["branches"] if b["name"]["en"] == branch)

    roms = branch["roms"]
    versions = list(roms.keys())
    versions.sort(key=version_key)

    version = versions[-1]
    rom = roms[version]

    archive_dir = os.path.join(vendor_root, "archive", codename, version)
    os.makedirs(archive_dir, exist_ok=True)

    recovery_path = os.path.join(archive_dir, rom["recovery"])
    if not os.path.isfile(recovery_path):
        print(
            f"downloading {xiaomi_mirror_url.format(version, rom['recovery'])} to {recovery_path}"
        )
        urlretrieve(xiaomi_mirror_url.format(version, rom["recovery"]), recovery_path)

    # Dump / extract-files.py
    subprocess.run(
        f"cd {device_tree_path} && ./extract-files.py {recovery_path} --keep-dump --only-target",
        shell=True,
        executable="/bin/bash",
    )

    # symlink to quickly interact with the latest dump
    dump_dir = os.path.join(vendor_root, codename)
    if os.path.isdir(dump_dir):
        os.unlink(dump_dir)
    os.symlink(os.path.join(archive_dir, Path(rom["recovery"]).stem), dump_dir)

    # update version in files
    for file in ["proprietary-files.txt", "proprietary-firmware.txt"]:
        with open(os.path.join(device_tree_path, file), "r", encoding="utf-8") as f:
            text = f.read()

        version_pattern = r"OS[.0-9]+[VW][LMN][A-Z]+((CN)|(MI))XM"
        text = re.sub(version_pattern, version, text)

        with open(os.path.join(device_tree_path, file), "w", encoding="utf-8") as f:
            f.write(text)

    # update build fingerprint and description
    with open(
        os.path.join(dump_dir, "META-INF", "com", "android", "metadata"), "r", encoding="utf-8"
    ) as f:
        text = f.read()

    build_fingerprint = re.search(r"(?<=post-build=)[-_a-zA-Z0-9/:.]+", text).group(0)
    build_desc = build_desc_from_fingerprint(build_fingerprint)

    with open(os.path.join(device_tree_path, f"lineage_{codename}.mk"), "r", encoding="utf-8") as f:
        text = f.read()

    text = re.sub(r"(?<=BuildFingerprint=)[-_a-zA-Z0-9/:.]+", build_fingerprint, text)
    text = re.sub(r'(?<=BuildDesc=").*(?=")', build_desc, text)

    with open(os.path.join(device_tree_path, f"lineage_{codename}.mk"), "w", encoding="utf-8") as f:
        f.write(text)

    # update vendor security patch level
    with open(os.path.join(dump_dir, "vendor", "build.prop"), "r", encoding="utf-8") as f:
        text = f.read()
    vendor_security_patch = re.search(r"(?<=ro.vendor.build.security_patch=)[-0-9]+", text).group(0)

    with open(os.path.join(device_tree_path, "BoardConfig.mk"), "r", encoding="utf-8") as f:
        text = f.read()

    text = re.sub(r"(?<=VENDOR_SECURITY_PATCH := )[-0-9]+", vendor_security_patch, text)

    with open(os.path.join(device_tree_path, "BoardConfig.mk"), "w", encoding="utf-8") as f:
        f.write(text)

    # Commit changes
    if device_tree_repo.is_dirty():
        device_tree_repo.git.add(A=True)
        device_tree_repo.index.commit(f"{codename}: Update blobs and firmware from {version}")

    if vendor_tree_repo.is_dirty(untracked_files=True):
        vendor_tree_repo.git.add(A=True)
        vendor_tree_repo.index.commit(f"{codename}: Update blobs and firmware from {version}")

    if "lineage" not in [r.name for r in device_tree_repo.remotes]:
        device_tree_repo.create_remote(
            "lineage", review_url.format(review_user, f"android_device_xiaomi_{codename}")
        )

    push_result = device_tree_repo.remote(name="lineage").push(f"HEAD:refs/for/{review_branch}")
    for info in push_result:
        if info.flags & info.ERROR:
            print(info.summary)
