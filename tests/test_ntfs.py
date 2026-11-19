from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dukatools import ntfs


# Trimmed from real `diskutil list -plist external` / `diskutil info -plist` output.
LIST_PLIST = {
    "AllDisksAndPartitions": [
        {
            "DeviceIdentifier": "disk4",
            "Content": "GUID_partition_scheme",
            "Partitions": [
                {"DeviceIdentifier": "disk4s1", "Content": "Microsoft Reserved", "Size": 16759808},
                {
                    "DeviceIdentifier": "disk4s2",
                    "Content": "Microsoft Basic Data",
                    "VolumeName": "ADATA HD710 PRO",
                    "Size": 2000382066688,
                },
            ],
        },
        {
            "DeviceIdentifier": "disk5",
            "Partitions": [{"DeviceIdentifier": "disk5s1", "VolumeName": "STICK", "Size": 32000000000}],
        },
    ]
}
INFOS = {
    "disk4s1": {},
    "disk4s2": {
        "FilesystemType": "ntfs",
        "VolumeName": "ADATA HD710 PRO",
        "MountPoint": "/Volumes/ADATA HD710 PRO",
        "Size": 2000382066688,
    },
    "disk5s1": {"FilesystemType": "exfat", "VolumeName": "STICK", "MountPoint": "/Volumes/STICK"},
}


class ParsingTests(unittest.TestCase):
    def test_only_ntfs_partitions_are_listed(self) -> None:
        parts = ntfs.parse_external_ntfs_partitions(LIST_PLIST, INFOS)
        self.assertEqual(len(parts), 1)
        p = parts[0]
        self.assertEqual(p.identifier, "disk4s2")
        self.assertEqual(p.device, "/dev/disk4s2")
        self.assertEqual(p.volume_name, "ADATA HD710 PRO")
        self.assertEqual(p.macos_mount_point, "/Volumes/ADATA HD710 PRO")

    def test_ntfs3g_processes_are_parsed_from_ps(self) -> None:
        ps = "\n".join(
            [
                "  412 /usr/libexec/fskitd",
                "13640 ntfs-3g /dev/disk4s2 /Volumes/ADATA -o uid=501,gid=20,umask=022,windows_names",
                "13700 /usr/local/bin/ntfs-3g /dev/rdisk6s1 /Volumes/OTHER",
                "13800 vim ntfs-3g.c",
            ]
        )
        mounts = ntfs.parse_ntfs3g_processes(ps)
        self.assertEqual(
            mounts,
            [
                ntfs.Ntfs3gMount(13640, "/dev/disk4s2", "/Volumes/ADATA"),
                ntfs.Ntfs3gMount(13700, "/dev/disk6s1", "/Volumes/OTHER"),
            ],
        )

    def test_mounted_paths_handles_spaces(self) -> None:
        out = "\n".join(
            [
                "/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)",
                "/dev/disk4s2 on /Volumes/ADATA HD710 PRO (ntfs, local, nodev, nosuid, read-only, fskit)",
                "fuse-t:/ADATA on /Volumes/ADATA (nfs)",
            ]
        )
        self.assertEqual(
            ntfs.mounted_paths(out),
            {"/", "/Volumes/ADATA HD710 PRO", "/Volumes/ADATA"},
        )


class HelperTests(unittest.TestCase):
    def test_normalize_device(self) -> None:
        for value in ("disk4s2", "/dev/disk4s2", "/dev/rdisk4s2", "rdisk4s2"):
            self.assertEqual(ntfs.normalize_device(value), "/dev/disk4s2")
        for bad in ("disk4", "/dev/disk", "sda1", "../etc"):
            with self.assertRaises(ntfs.NtfsError):
                ntfs.normalize_device(bad)

    def test_sanitize_volume_name(self) -> None:
        self.assertEqual(ntfs.sanitize_volume_name("ADATA HD710 PRO"), "ADATA_HD710_PRO")
        self.assertEqual(ntfs.sanitize_volume_name("Мой диск"), "NTFS")
        self.assertEqual(ntfs.sanitize_volume_name("../evil"), "evil")
        self.assertEqual(ntfs.sanitize_volume_name("data.v2"), "data.v2")

    def test_mount_options(self) -> None:
        opts = ntfs.build_mount_options(501, 20, "ADATA")
        self.assertEqual(opts, "uid=501,gid=20,umask=022,windows_names,hide_dot_files,volname=ADATA")
        self.assertNotIn("noappledouble", opts)
        self.assertTrue(ntfs.build_mount_options(501, 20, "X", read_only=True).endswith(",ro"))

    def test_human_size(self) -> None:
        self.assertEqual(ntfs.human_size(2000382066688), "2.0 TB")
        self.assertEqual(ntfs.human_size(512), "512 B")


class BinaryCheckTests(unittest.TestCase):
    def test_user_owned_binary_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            b = Path(td) / "ntfs-3g"
            b.write_text("#!/bin/sh\n")
            b.chmod(0o755)
            self.assertEqual(ntfs.binary_problem(b), "not owned by root")
            self.assertEqual(ntfs.binary_problem(Path(td) / "missing"), "not installed")


class SetupHelperTests(unittest.TestCase):
    def test_sha256_and_extract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "a.tgz"
            data = b"configure script"
            with tarfile.open(archive, "w:gz") as tf:
                info = tarfile.TarInfo("ntfs-3g-1.0/configure")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            self.assertEqual(len(ntfs.sha256_file(archive)), 64)
            out = root / "out"
            out.mkdir()
            src = ntfs.extract(archive, out)
            self.assertEqual(src.name, "ntfs-3g-1.0")
            self.assertEqual((src / "configure").read_bytes(), data)


class FuseTDetectionTests(unittest.TestCase):
    def test_leftover_library_without_servers_is_not_fuse_t(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lib = root / "libfuse-t.dylib"
            lib.write_text("")
            (root / "fuse-t.pc").write_text("Name: fuse-t\nVersion: 1.2.9\n")
            support = root / "support"
            with mock.patch.object(ntfs, "FUSE_T_LIB", lib), \
                 mock.patch.object(ntfs, "FUSE_T_PKGCONFIG_DIR", root), \
                 mock.patch.object(ntfs, "FUSE_T_SUPPORT_DIR", support):
                self.assertIsNone(ntfs.fuse_t_version())  # what `brew uninstall --cask fuse-t` leaves
                support.mkdir()
                self.assertEqual(ntfs.fuse_t_version(), "1.2.9")


class PrerequisiteTests(unittest.TestCase):
    def test_confirm_without_terminal_is_no(self) -> None:
        with mock.patch.object(ntfs.sys.stdin, "isatty", return_value=False):
            self.assertFalse(ntfs.confirm("Install?"))
        self.assertTrue(ntfs.confirm("Install?", assume_yes=True))

    def test_declined_fuse_t_install_stops_without_running_brew(self) -> None:
        with mock.patch.object(ntfs, "fuse_t_version", return_value=None), \
             mock.patch.object(ntfs.shutil, "which", return_value="/opt/homebrew/bin/brew"), \
             mock.patch.object(ntfs, "confirm", return_value=False), \
             mock.patch.object(ntfs, "run") as run:
            with self.assertRaises(ntfs.NtfsError) as ctx:
                ntfs.ensure_prerequisites()
            run.assert_not_called()
            self.assertIn("macos-fuse-t/cask/fuse-t", str(ctx.exception))

    def test_missing_homebrew_explains(self) -> None:
        with mock.patch.object(ntfs, "fuse_t_version", return_value=None), \
             mock.patch.object(ntfs.shutil, "which", return_value=None):
            with self.assertRaises(ntfs.NtfsError) as ctx:
                ntfs.ensure_prerequisites()
            self.assertIn("brew.sh", str(ctx.exception))


def part(identifier: str, name: str = "DATA", macos: str = "") -> ntfs.Partition:
    return ntfs.Partition(identifier, name, 1, macos)


class MultiDiskTests(unittest.TestCase):
    A = part("disk4s2", "ADATA")
    B = part("disk6s1", "ADATA")

    def test_single_partition_needs_no_argument(self) -> None:
        self.assertEqual(ntfs.choose_partition([self.A], None), self.A)

    def test_two_free_partitions_need_an_argument(self) -> None:
        with self.assertRaises(ntfs.NtfsError) as ctx:
            ntfs.choose_partition([self.A, self.B], None)
        self.assertIn("disk4s2", str(ctx.exception))
        self.assertIn("disk6s1", str(ctx.exception))
        self.assertEqual(ntfs.choose_partition([self.A, self.B], "disk6s1"), self.B)

    def test_the_only_unmounted_partition_is_picked(self) -> None:
        self.assertEqual(ntfs.choose_partition([self.A, self.B], None, {"/dev/disk4s2"}), self.B)
        self.assertIsNone(ntfs.choose_partition([self.A, self.B], None, {"/dev/disk4s2", "/dev/disk6s1"}))

    def test_taken_name_gets_a_suffix(self) -> None:
        taken = {"/Volumes/ADATA", "/Volumes/ADATA_2"}
        self.assertEqual(ntfs.choose_volname("ADATA", taken.__contains__), "ADATA_3")
        self.assertEqual(ntfs.choose_volname("OTHER", taken.__contains__), "OTHER")

    def test_explicit_taken_name_is_an_error(self) -> None:
        with self.assertRaises(ntfs.NtfsError):
            ntfs.choose_volname("ADATA", {"/Volumes/ADATA"}.__contains__, explicit=True)

    def test_mount_point_in_use(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td) / "empty"
            empty.mkdir()
            full = Path(td) / "full"
            full.mkdir()
            (full / "f").write_text("x")
            self.assertFalse(ntfs.mount_point_in_use(str(empty), set()))
            self.assertFalse(ntfs.mount_point_in_use(str(Path(td) / "missing"), set()))
            self.assertTrue(ntfs.mount_point_in_use(str(full), set()))
            self.assertTrue(ntfs.mount_point_in_use(str(empty), {str(empty)}))


class UnmountPlanTests(unittest.TestCase):
    S2 = ntfs.Ntfs3gMount(1, "/dev/disk4s2", "/Volumes/A")
    S3 = ntfs.Ntfs3gMount(2, "/dev/disk4s3", "/Volumes/B")
    OTHER = ntfs.Ntfs3gMount(3, "/dev/disk6s1", "/Volumes/C")
    ALL = [S2, S3, OTHER]

    def test_whole_disk(self) -> None:
        self.assertEqual(ntfs.whole_disk("/dev/disk4s2"), "/dev/disk4")
        self.assertEqual(ntfs.whole_disk("/dev/disk12s10"), "/dev/disk12")

    def test_target_by_path_or_device(self) -> None:
        self.assertEqual(ntfs.plan_unmount(self.ALL, "/Volumes/B/"), [self.S3])
        self.assertEqual(ntfs.plan_unmount(self.ALL, "disk6s1"), [self.OTHER])
        with self.assertRaises(ntfs.NtfsError):
            ntfs.plan_unmount(self.ALL, "/Volumes/X")

    def test_several_mounts_need_a_target_or_all(self) -> None:
        with self.assertRaises(ntfs.NtfsError):
            ntfs.plan_unmount(self.ALL, None)
        self.assertEqual(ntfs.plan_unmount(self.ALL, None, all_mounts=True), self.ALL)
        with self.assertRaises(ntfs.NtfsError):
            ntfs.plan_unmount(self.ALL, "/Volumes/A", all_mounts=True)

    def test_eject_includes_other_partitions_of_the_same_disk(self) -> None:
        self.assertEqual(ntfs.plan_unmount(self.ALL, "/Volumes/A"), [self.S2])
        self.assertEqual(ntfs.plan_unmount(self.ALL, "/Volumes/A", eject=True), [self.S2, self.S3])
        self.assertEqual(ntfs.plan_unmount(self.ALL, "/Volumes/C", eject=True), [self.OTHER])

    def test_eject_sibling_is_unmounted_before_disk_is_ejected(self) -> None:
        calls: list[list[str]] = []
        alive = {1, 2, 3}

        def fake_sudo(cmd, **kw):
            calls.append(list(cmd))
            alive.discard({"/Volumes/A": 1, "/Volumes/B": 2, "/Volumes/C": 3}[cmd[1]])
            return mock.Mock(returncode=0, stdout="")

        def fake_run(cmd, **kw):
            calls.append(list(cmd))
            return mock.Mock(returncode=0, stdout="")

        with mock.patch.object(ntfs, "require_macos"), \
             mock.patch.object(ntfs, "ntfs3g_mounts", side_effect=lambda: [m for m in self.ALL if m.pid in alive]), \
             mock.patch.object(ntfs, "run_sudo", side_effect=fake_sudo), \
             mock.patch.object(ntfs, "run", side_effect=fake_run), \
             mock.patch.object(ntfs.os, "geteuid", return_value=501):
            self.assertEqual(ntfs.unmount("/Volumes/A", eject=True), 0)
        relevant = [c for c in calls if c[0] != "sync"]
        self.assertEqual(
            relevant,
            [["umount", "/Volumes/A"], ["umount", "/Volumes/B"], ["diskutil", "eject", "/dev/disk4"]],
        )
        self.assertEqual(alive, {3})


class PlatformTests(unittest.TestCase):
    def test_non_macos_commands_explain_and_fail(self) -> None:
        with mock.patch.object(ntfs.platform, "system", return_value="Linux"):
            self.assertEqual(ntfs.main(["doctor"]), 0)
            self.assertEqual(ntfs.main(["mount"]), 1)
            self.assertEqual(ntfs.main(["setup"]), 1)

    def test_running_as_root_is_refused(self) -> None:
        with mock.patch.object(ntfs.os, "geteuid", return_value=0):
            with self.assertRaises(ntfs.NtfsError):
                ntfs.refuse_root()


if __name__ == "__main__":
    unittest.main()
