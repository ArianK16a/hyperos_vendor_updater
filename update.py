import json
import os.path
import re
import subprocess
from urllib.request import urlopen, urlretrieve
from pathlib import Path
from git import Repo, GitCommandError

GL_STABLE = "Xiaomi HyperOS Global Stable"
CN_STABLE = "Xiaomi HyperOS Stable"

devices = [
    ("cupid", GL_STABLE),
    ("zeus", GL_STABLE),
    ("mayfly", CN_STABLE),
    ("unicorn", CN_STABLE),
    ("thor", CN_STABLE),
    ("diting", GL_STABLE),
    ("zizhan", CN_STABLE),
    ("marble", GL_STABLE),
    ("mondrian", GL_STABLE),
]

android_root = "/home/arian/android/lineage-23/"
vendor_root = "/home/arian/android/vendor/sm8450/"

review_url = "ssh://{}@review.lineageos.org:29418/LineageOS/{}"
review_user = "ArianK16a"
review_branch = "lineage-23.0"

hos_fans_url = "https://raw.githubusercontent.com/HegeKen/HyperData/refs/heads/main/devices/{}.json"
xiaomi_mirror_url = "https://bkt-sgp-miui-ota-update-alisgp.oss-ap-southeast-1.aliyuncs.com/{}/{}"

hos_version_pattern = r"OS[.0-9]+[VW][LMN][A-Z]+((CN)|(MI))XM"

# fingerprint format: brand/name/device:release/id/incremental:type/tags
build_fingerprint_format = "{}/{}/{}:{}/{}/{}:{}/{}"
# build desc format: name-type release id incremental keys
build_desc_format = "{}-{} {} {} {} {}"


def version_key(version):
    # split by digit sequences
    return [int(i) for i in re.split(r"(\d+)", version) if i.isdigit()]


for codename, branch in devices:
    device_tree_path = os.path.join(android_root, "device", "xiaomi", codename)
    device_tree_repo = Repo(device_tree_path)
    if device_tree_repo.is_dirty(untracked_files=True):
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
    dump_dir = os.path.join(archive_dir, Path(rom["recovery"]).stem)
    os.makedirs(archive_dir, exist_ok=True)

    recovery_path = os.path.join(archive_dir, rom["recovery"])
    if not os.path.isfile(recovery_path):
        print(
            f"downloading {xiaomi_mirror_url.format(version, rom['recovery'])} to {recovery_path}"
        )
        urlretrieve(xiaomi_mirror_url.format(version, rom["recovery"]), recovery_path)
    elif os.path.isdir(dump_dir):
        with open(
            os.path.join(device_tree_path, "proprietary-files.txt"), "r", encoding="utf-8"
        ) as f:
            text = f.read()
        if re.search(hos_version_pattern, text).group(0) == version:
            print(f"{codename} is already update to {version}")
            continue

    # Dump / extract-files.py
    subprocess.run(
        f"{android_root}/tools/extract-utils/extract.py {recovery_path}",
        shell=True,
        executable="/bin/bash",
    )
    subprocess.run(
        f"cd {device_tree_path} && ./extract-files.py {recovery_path} --keep-dump --only-target",
        shell=True,
        executable="/bin/bash",
    )

    # symlink to quickly interact with the latest dump
    dump_link = os.path.join(vendor_root, codename)
    if os.path.isdir(dump_link):
        os.unlink(dump_link)
    os.symlink(os.path.join(archive_dir, Path(rom["recovery"]).stem), dump_link)

    # update version in files
    for file in ["proprietary-files.txt", "proprietary-firmware.txt"]:
        with open(os.path.join(device_tree_path, file), "r", encoding="utf-8") as f:
            text = f.read()

        text = re.sub(hos_version_pattern, version, text, count=1)

        with open(os.path.join(device_tree_path, file), "w", encoding="utf-8") as f:
            f.write(text)

    # update build fingerprint and description
    with open(
        os.path.join(dump_dir, "META-INF", "com", "android", "metadata"), "r", encoding="utf-8"
    ) as f:
        text = f.read()

    # Load build properties, priority from low to high
    build_properties = {}
    for prop_file in [
        os.path.join(dump_dir, "product", "etc", "build.prop"),
        os.path.join(dump_dir, "vendor", "build.prop"),
        os.path.join(dump_dir, "vendor", f"{codename}_build.prop"),
    ]:
        if os.path.isfile(prop_file):
            with open(prop_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("import"):
                        continue

                    key, value = line.split("=", 1)
                    key, value = key.strip(), value.strip()
                    build_properties[key] = value

    build_fingerprint = build_fingerprint_format.format(
        build_properties["ro.product.vendor.brand"],
        build_properties["ro.product.vendor.name"],
        build_properties["ro.product.vendor.device"],
        build_properties["ro.product.build.version.release"],
        build_properties["ro.product.build.id"],
        build_properties["ro.product.build.version.incremental"],
        build_properties["ro.product.build.type"],
        build_properties["ro.product.build.tags"],
    )
    build_desc = build_desc_format.format(
        build_properties["ro.product.vendor.name"],
        build_properties["ro.product.build.type"],
        build_properties["ro.product.build.version.release"],
        build_properties["ro.product.build.id"],
        build_properties["ro.product.build.version.incremental"],
        build_properties["ro.product.build.tags"],
    )

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
    if device_tree_repo.is_dirty(untracked_files=True):
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
